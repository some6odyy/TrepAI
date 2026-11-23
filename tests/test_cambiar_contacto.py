"""Cambiar el teléfono / correo / nombre de los recordatorios de una cita ya
agendada, con IA (marcador [[CONTACTO|...]]) y sin IA. Reproduce la
conversación real de Ignacio.
    PYTHONPATH=. python tests/test_cambiar_contacto.py
"""
import os, tempfile, uuid
from datetime import date, datetime, time

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"
os.environ["AI_API_KEY"] = "clave-falsa"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import (  # noqa: E402
    agendado_ia, ai_service, reserva_sin_ia as R, whatsapp_service, notificacion_service, email_service,
)

HOY = date(2026, 11, 22)  # domingo
AHORA = datetime(2026, 11, 22, 18, 22)


class S:  # servicio falso para los extractores
    def __init__(self, n): self.nombre_servicio = n


def test_extractores():
    servicios = [S("Corte clásico"), S("Degradado")]
    wa = "56981407529"
    c = R.extraer_cambio_contacto("mi teléfono es +56 9 8140 7529", servicios, HOY, wa)
    assert c.telefono == "56981407529" and not c.correo and not c.nombre
    c = R.extraer_cambio_contacto("cambia mi correo a ignacio@gmail.com", servicios, HOY, wa)
    assert c.correo == "ignacio@gmail.com" and not c.telefono
    c = R.extraer_cambio_contacto("mejor mándalo a este número", servicios, HOY, wa)
    assert c.telefono == wa
    c = R.extraer_cambio_contacto("a nombre de Ignacio Pérez", servicios, HOY, wa)
    assert c.nombre == "Ignacio Pérez"
    assert R.extraer_cambio_contacto("mi teléfono es 11111111", servicios, HOY, wa).telefono_invalido
    # Día/hora/servicio = es un cambio de hora, no de datos
    assert R.extraer_cambio_contacto("Degradado el martes a las 12", servicios, HOY, wa) is None
    assert R.extraer_cambio_contacto("hola", servicios, HOY, wa) is None
    assert R.extraer_cambio_contacto("¿dónde quedan?", servicios, HOY, wa) is None
    assert R.telefono_invalido_mencionado("mi número es 11111111")
    assert not R.telefono_invalido_mencionado("mi número es 912345678")
    # Errores vistos en la conversación real
    exc = {"corte", "clasico"}
    texto = "hora : 25\nnombre: Ignacio\ncorreo : correo@gmail.com"
    assert R.extraer_nombre(texto, exc) == "Ignacio"
    assert R.extraer_nombre("falta algo mas", exc) is None
    # Marcador de la IA
    limpio, sol = agendado_ia.extraer_solicitud("[[CONTACTO|-|-|56981407529]]")
    assert limpio == "" and sol.accion == "CONTACTO" and sol.telefono == "56981407529"
    assert sol.nombre is None and sol.correo is None


def _preparar(sufijo):
    c = TestClient(app)
    enviados = []
    whatsapp_service.enviar_mensaje = lambda **k: enviados.append(k["texto"]) or True
    notificacion_service.enviar_whatsapp_cloud = lambda *a: True
    email_service.enviar_correo = lambda *a, **k: True
    ai_service.ahora_chile = lambda: AHORA

    c.post("/auth/registro", json={"nombre": "Dueño", "correo": f"contacto{sufijo}@test.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": f"contacto{sufijo}@test.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Sin Pelos", "horario": "Lun-Sáb 10:00-21:30"}).json()["id_negocio"]
    for n, p, d in [("Corte clásico", 10000, 30), ("Degradado", 12000, 45)]:
        c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": n, "precio": p, "duracion_estimada": d})
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": f"PNID-CONT{sufijo}", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)

    def decir(texto, tel="56981407529"):
        enviados.clear()
        c.post("/webhook", json={"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": f"PNID-CONT{sufijo}"},
               "contacts": [{"profile": {"name": "IL"}}],
               "messages": [{"from": tel, "type": "text", "text": {"body": texto}}]}}]}]})
        return enviados[-1]

    return c, h, idn, decir


def test_sin_ia():
    c, h, idn, decir = _preparar("a")
    ai_service.generar_respuesta_o_none = lambda *a, **k: None   # la IA no responde

    decir("hola")
    decir("corte clásico")
    # Muchos mensajes de por medio: el servicio no se debe olvidar
    for _ in range(6):
        decir("ok")
    r = decir("el viernes a las 14")
    assert "Corte clásico" in r, r
    r = decir("hora : 25\nnombre: Ignacio\ncorreo : correo@gmail.com")
    assert "nombre: Ignacio" in r and "Correo," not in r, r
    r = decir("mi teléfono es 11111111")
    assert "no parece válido" in r, r
    r = decir("mi teléfono es 912345678")
    assert "Reservé" in r, r
    cita = c.get(f"/negocio/{idn}/agenda", headers=h).json()[0]
    assert cita["telefono_contacto"] == "56912345678"

    # Lo que falló en la prueba real: cambiar el número después de reservar
    r = decir("mi teléfono es +56 9 8140 7529")
    assert "Actualicé" in r and "+56 9 8140 7529" in r, r
    cita = c.get(f"/negocio/{idn}/agenda", headers=h).json()[0]
    assert cita["telefono_contacto"] == "56981407529", cita
    # Mismo número otra vez -> nada que cambiar
    assert "nada que cambiar" in decir("mi número es +56 9 8140 7529")
    # Correo nuevo
    r = decir("cambia mi correo a ignacio.p@gmail.com")
    assert "Actualicé" in r and "ignacio.p@gmail.com" in r, r
    assert c.get(f"/negocio/{idn}/agenda", headers=h).json()[0]["correo_cliente"] == "ignacio.p@gmail.com"
    # Número de otra persona -> se avisa que mande un hola desde ese número
    r = decir("mejor envíalo al +56 9 1111 2222")
    assert "Actualicé" in r and "hola" in r, r
    # El dueño ve el aviso
    avisos = c.get(f"/negocio/{idn}/novedades", headers=h).json()["avisos"]
    assert any(a["tipo"] == "contacto_actualizado" for a in avisos), avisos
    # Otro cliente sin hora agendada
    r = decir("mi teléfono es +56 9 3333 4444", tel="56955556666")
    assert "Actualicé" not in r, r


def test_con_ia():
    c, h, idn, decir = _preparar("b")
    ai_service.generar_respuesta_o_none = lambda *a, **k: None
    r = decir("Corte clásico el viernes a las 14, Ignacio, correo@gmail.com, 912345678", tel="56977778888")
    assert "Reservé" in r, r
    # La IA responde con el marcador; el backend lo ejecuta y arma el mensaje
    ai_service.generar_respuesta_o_none = lambda *a, **k: "[[CONTACTO|-|-|56977778888]]"
    r = decir("mejor a este mismo número", tel="56977778888")
    assert "Actualicé" in r and "[[" not in r, r
    citas = [x for x in c.get(f"/negocio/{idn}/agenda", headers=h).json() if x["telefono_contacto"] == "56977778888"]
    assert len(citas) == 1
    # Con el agendado automático apagado también funciona
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": False})
    ai_service.generar_respuesta_o_none = lambda *a, **k: "[[CONTACTO|Ignacio Pérez|-|-]]"
    r = decir("pon la hora a nombre de Ignacio Pérez", tel="56977778888")
    assert "Ignacio Pérez" in r and "[[" not in r, r


if __name__ == "__main__":
    test_extractores()
    test_sin_ia()
    test_con_ia()
    print("OK: cambiar datos de contacto")
