"""El bot responde siempre:
- 429/503 pasajeros se reintentan y, si siguen, se usa un modelo de respaldo;
- si ningún modelo responde, contesta con los datos del negocio (sin IA)
  y deja un aviso al dueño.
No llama a ninguna API real: requests.post se reemplaza.
    PYTHONPATH=. python tests/test_ia_respaldo.py
"""
import os, tempfile, uuid

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"
os.environ["AI_PROVIDER"] = "gemini"
os.environ["AI_API_KEY"] = "clave-falsa"

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core import config  # noqa: E402
from app.services import ai_service, respuesta_sin_ia, whatsapp_service  # noqa: E402

ai_service.time.sleep = lambda s: None  # sin esperas reales


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text
        self.ok = 200 <= status < 300

    def json(self):
        return self._data


def ok(texto):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": texto}]}}]})


LLAMADAS = []


def simular(respuestas):
    """respuestas: lista de Resp o excepciones, en orden de llamada."""
    cola = list(respuestas)
    LLAMADAS.clear()

    def post(url, headers=None, json=None, timeout=None):
        LLAMADAS.append(url.split("/models/")[-1].split(":")[0] if "/models/" in url else url)
        r = cola.pop(0) if cola else Resp(503, text="saturado")
        if isinstance(r, Exception):
            raise r
        return r
    ai_service.requests.post = post


def test_motor():
    plan = ai_service._plan_de_intentos("gemini", "gemini-3.6-flash")
    assert plan[0] == ("gemini", "gemini-3.6-flash") and ("gemini", "gemini-3.5-flash-lite") in plan
    assert all(p == "gemini" for p, _ in plan)  # sin clave de OpenAI no se intenta OpenAI
    # negocio que eligió OpenAI pero solo hay clave de Gemini -> usa Gemini
    assert ai_service._plan_de_intentos("openai", "gpt-5.5")[0][0] == "gemini"

    # 429 una vez y luego responde con el mismo modelo
    simular([Resp(429, text="quota"), ok("Hola!")])
    assert ai_service.generar_respuesta_o_none("p", "gemini", "gemini-3.6-flash") == "Hola!"
    assert LLAMADAS == ["gemini-3.6-flash", "gemini-3.6-flash"]

    # 503 dos veces -> modelo de respaldo
    simular([Resp(503), Resp(503), ok("Desde el respaldo")])
    assert ai_service.generar_respuesta_o_none("p", "gemini", "gemini-3.6-flash") == "Desde el respaldo"
    assert LLAMADAS[-1] == "gemini-3.5-flash-lite", LLAMADAS

    # timeout -> pasa directo al siguiente modelo
    simular([requests.Timeout(), ok("ok")])
    assert ai_service.generar_respuesta_o_none("p", "gemini", "gemini-3.6-flash") == "ok"

    # respuesta bloqueada / vacía -> siguiente modelo
    simular([Resp(200, {"promptFeedback": {"blockReason": "SAFETY"}}), ok("ok2")])
    assert ai_service.generar_respuesta_o_none("p", "gemini", "gemini-3.6-flash") == "ok2"

    # clave inválida (400/403, no pasajero): no insiste con el mismo modelo
    simular([Resp(403, text="API key not valid")] * 10)
    assert ai_service.generar_respuesta_o_none("p", "gemini", "gemini-3.6-flash") is None
    assert len(LLAMADAS) == len(ai_service._plan_de_intentos("gemini", "gemini-3.6-flash"))

    # todo falla
    simular([])
    assert ai_service.generar_respuesta_o_none("p") is None


def test_respuesta_sin_ia():
    perfil = {"nombre_negocio": "Barbería Sin Pelos", "horario": "Lun-Sáb 10:00-19:00", "direccion": "La Lengua 123"}
    cat = [{"nombre_servicio": "Degradado", "precio": 8000, "duracion_estimada": 30},
           {"nombre_servicio": "Barba", "precio": 5000, "duracion_estimada": 20}]
    r = respuesta_sin_ia.generar("Necesito saber los servicios antes de elegir uno", perfil, cat)
    assert "Degradado" in r and "$8.000" in r and "no puedo" not in r.lower(), r
    r = respuesta_sin_ia.generar("a qué hora cierran y dónde quedan?", perfil, cat)
    assert "10:00-19:00" in r and "La Lengua 123" in r
    r = respuesta_sin_ia.generar("quiero agendar", perfil, cat, nombre_cliente="Yohel Faundez")
    assert "Hola Yohel" in r and "correo" in r
    r = respuesta_sin_ia.generar("jajaja", perfil, cat)
    assert "Barbería Sin Pelos" in r and "Degradado" in r
    assert "Degradado" not in respuesta_sin_ia.generar("servicios?", perfil, cat, catalogo_habilitado=False)


def test_webhook_responde_siempre():
    c = TestClient(app)
    enviados = []
    whatsapp_service.enviar_mensaje = lambda **k: enviados.append(k["texto"]) or True
    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "ia@resp.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "ia@resp.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Sin Pelos", "horario": "Lun-Sáb 10:00-19:00"}).json()["id_negocio"]
    c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": "Degradado", "precio": 8000, "duracion_estimada": 30})
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": "PNID-IA", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)

    simular([])  # la IA no responde nunca
    ev = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PNID-IA"},
          "contacts": [{"profile": {"name": "Yohel"}}],
          "messages": [{"from": "56967158624", "type": "text", "text": {"body": "Necesito saber los servicios"}}]}}]}]}
    assert c.post("/webhook", json=ev).json()["status"] == "procesado"
    c.post("/webhook", json=ev)
    assert len(enviados) == 2 and "Degradado" in enviados[0] and "no puedo procesar" not in enviados[0], enviados
    avisos = [a for a in c.get(f"/negocio/{idn}/novedades", headers=h).json()["avisos"] if a["tipo"] == "ia_no_disponible"]
    assert len(avisos) == 1, avisos  # no se repite mientras no se lea


if __name__ == "__main__":
    test_motor()
    test_respuesta_sin_ia()
    test_webhook_responde_siempre()
    print("OK: el bot responde siempre (reintentos, respaldo y respuesta sin IA)")
