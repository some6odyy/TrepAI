"""Reserva sin IA: fechas pasadas, fechas que no calzan con el día de la
semana, fechas con espacios o año, y respuestas como "ok" a mitad de una
reserva. Basado en la conversación real de Ignacio.
    PYTHONPATH=. python tests/test_fechas_sin_ia.py
"""
import os, tempfile, uuid
from datetime import date, datetime, time

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"
os.environ["AI_API_KEY"] = "clave-falsa"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_service, reserva_sin_ia as R, whatsapp_service, notificacion_service, email_service  # noqa: E402

HOY = date(2026, 11, 22)  # domingo
AHORA = datetime(2026, 11, 22, 18, 22)


def test_fechas():
    f, p = R.analizar_fecha("sábado 21 - 11", HOY)
    assert f is None and "ya pasó" in p and "28-11" in p, p
    f, p = R.analizar_fecha("Sábado 29 - 11 - 2027", HOY)
    assert f is None and "lunes" in p and "sábado 27-11-2027" in p, p
    assert R.analizar_fecha("lunes 29-11-2027", HOY) == (date(2027, 11, 29), None)
    assert R.analizar_fecha("29 de noviembre", HOY) == (date(2026, 11, 29), None)
    assert R.analizar_fecha("sábado 28 de noviembre", HOY) == (date(2026, 11, 28), None)
    assert R.analizar_fecha("el 25 / 11", HOY) == (date(2026, 11, 25), None)
    assert R.analizar_fecha("05-01", HOY) == (date(2027, 1, 5), None)  # enero = el próximo año
    assert R.analizar_fecha("21-11 15:00", HOY)[1] is not None
    assert R.extraer_fecha("+56 9 1234-5678", HOY) is None
    assert R.extraer_fecha("10:00-20:00", HOY) is None
    assert R.extraer_hora("hora: 15") == time(15, 0)
    assert R.extraer_hora("hora : 25") is None and R.hora_invalida_mencionada("hora : 25")
    assert R.hora_invalida_mencionada("hora 24") and not R.hora_invalida_mencionada("a las 12")
    assert R.extraer_hora("1 hora antes") is None
    assert R.fecha_legible(date(2027, 11, 27), HOY) == "sábado 27-11-2027"


def test_conversacion():
    c = TestClient(app)
    enviados = []
    whatsapp_service.enviar_mensaje = lambda **k: enviados.append(k["texto"]) or True
    notificacion_service.enviar_whatsapp_cloud = lambda *a: True
    email_service.enviar_correo = lambda *a, **k: True
    ai_service.generar_respuesta_o_none = lambda *a, **k: None   # la IA no responde
    ai_service.ahora_chile = lambda: AHORA

    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "fechas@test.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "fechas@test.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Sin Pelos", "horario": "Lun-Sáb 10:00-21:30"}).json()["id_negocio"]
    c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": "Corte clásico", "precio": 10000, "duracion_estimada": 30})
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": "PNID-FECHAS", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)

    def decir(texto, tel="56981407529"):
        enviados.clear()
        c.post("/webhook", json={"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PNID-FECHAS"},
               "contacts": [{"profile": {"name": "IL"}}],
               "messages": [{"from": tel, "type": "text", "text": {"body": texto}}]}}]}]})
        return enviados[-1]

    # Sin reserva en curso, "ok" sigue siendo un agradecimiento
    assert "De nada" in decir("ok", tel="56900000001")

    decir("hola")
    decir("corte clásico")
    r = decir("sábado 21 - 11")
    assert "ya pasó" in r and "28-11" in r, r
    r = decir("ok")  # acepta la fecha propuesta
    assert "sábado 28-11" in r and "De nada" not in r, r
    r = decir("Sábado 29 - 11 - 2027")
    assert "cae *lunes*" in r, r
    r = decir("???")
    assert "Sigamos con tu reserva" in r and "sábado 28-11" in r, r
    r = decir("hora : 25\nnombre: Ignacio\ncorreo : correo@gmail.com")
    assert "Esa hora no existe" in r and "nombre: Ignacio" in r, r
    r = decir("falta algo mas")
    assert "Sigamos con tu reserva" in r, r
    # Una pregunta por el horario a mitad de la reserva se responde normal
    assert "Atendemos" in decir("¿cuál es el horario?")
    r = decir("a las 12, mi teléfono es 912345678")
    assert "Reservé" in r and "sábado 28-11" in r, r
    # Con año distinto al actual, el mensaje lo muestra
    r = decir("Corte clásico el lunes 29-11-2027 a las 12, Ana, ana@correo.cl, 911112222", tel="56911112222")
    assert "Reservé" in r and "29-11-2027" in r, r


if __name__ == "__main__":
    test_fechas()
    test_conversacion()
    print("OK: fechas y respuestas sin IA")
