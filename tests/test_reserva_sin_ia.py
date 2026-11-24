"""Agendar sin IA: los datos se juntan entre mensajes y se reserva con las
mismas reglas que el agendado con IA. Reproduce la conversación real.
    PYTHONPATH=. python tests/test_reserva_sin_ia.py
"""
import os, tempfile, uuid
from datetime import date, datetime, time, timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"
os.environ["AI_API_KEY"] = "clave-falsa"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_service, reserva_sin_ia as R, whatsapp_service, notificacion_service, email_service  # noqa: E402

HOY = date(2026, 11, 22)  # domingo
AHORA = datetime(2026, 11, 22, 16, 55)


class S:  # servicio falso para los extractores
    def __init__(self, n): self.nombre_servicio = n


def test_extractores():
    assert R.extraer_hora("Mañana a las 18") == time(18, 0)
    assert R.extraer_hora("a las 6 de la tarde") == time(18, 0)
    assert R.extraer_hora("tipo 17:30") == time(17, 30)
    assert R.extraer_hora("a las 11") == time(11, 0)
    assert R.extraer_hora("+56967158624") is None
    assert R.extraer_fecha("Mañana a las 18", HOY) == date(2026, 11, 23)
    assert R.extraer_fecha("en la mañana del martes", HOY) == date(2026, 11, 24)
    assert R.extraer_fecha("el 25/11", HOY) == date(2026, 11, 25)
    assert R.extraer_fecha("pasado mañana", HOY) == date(2026, 11, 24)
    assert R.extraer_correo("yohelignacio21@gmail.com") == "yohelignacio21@gmail.com"
    assert R.extraer_telefono("+56967158624 yohel@gmail.com") == "56967158624"
    assert R.extraer_telefono("967158624") == "56967158624"
    assert R.extraer_telefono("el 25-11-2026") is None
    exc = {"degradado", "con", "diseño", "barba"}
    assert R.extraer_nombre("Yohel\nyohel@gmail.com\n967158624", exc) == "Yohel"
    assert R.extraer_nombre("me llamo juan pérez", exc) == "Juan Pérez"
    assert R.extraer_nombre("Degradado", exc) is None
    assert R.extraer_nombre("Hola", exc) is None
    assert R.extraer_nombre("Mañana a las 18", exc) is None
    assert R.extraer_nombre("Catalogo", exc) is None
    servicios = [S("Degradado"), S("Degradado con diseño"), S("Barba completa")]
    assert R.extraer_servicio("degradado con diseño mañana", servicios).nombre_servicio == "Degradado con diseño"


def test_conversacion_real():
    c = TestClient(app)
    enviados = []
    whatsapp_service.enviar_mensaje = lambda **k: enviados.append(k["texto"]) or True
    notificacion_service.enviar_whatsapp_cloud = lambda *a: True
    email_service.enviar_correo = lambda *a, **k: True
    ai_service.generar_respuesta_o_none = lambda *a, **k: None   # la IA no responde
    ai_service.ahora_chile = lambda: AHORA

    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "sinia@test.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "sinia@test.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Sin Pelos", "horario": "Lun-Sáb 10:00-21:30"}).json()["id_negocio"]
    for n, p, d in [("Degradado", 12000, 45), ("Degradado con diseño", 14000, 45), ("Barba completa", 9000, 30)]:
        c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": n, "precio": p, "duracion_estimada": d})
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": "PNID-SINIA", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)

    def decir(texto, tel="56967158624"):
        enviados.clear()
        c.post("/webhook", json={"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PNID-SINIA"},
               "contacts": [{"profile": {"name": "Yohel🤠"}}],
               "messages": [{"from": tel, "type": "text", "text": {"body": texto}}]}}]}]})
        return enviados[-1]

    decir("Hola")
    assert "Degradado con diseño" in decir("Catalogo")
    r = decir("Degradado con diseño")
    assert "$14.000" in r and "día y hora" in r, r
    r = decir("Mañana a las 18")
    assert "Anoté: *Degradado con diseño*, el lunes 23-11, a las 18:00" in r and "nombre" in r and "correo" in r, r
    r = decir("Yohel\nyohelignacio21@gmail.com\n967158624")
    assert "Reservé" in r and "Degradado con diseño" in r, r

    citas = c.get(f"/negocio/{idn}/agenda", headers=h).json()
    assert len(citas) == 1, citas
    cita = citas[0]
    assert cita["fecha_cita"] == "2026-11-23" and cita["hora_cita"].startswith("18:00")
    assert cita["correo_cliente"] == "yohelignacio21@gmail.com" and cita["telefono_contacto"] == "56967158624"
    assert cita["nombre_cliente"] == "Yohel"

    # Después de reservar, los datos viejos no se reutilizan: un saludo es un saludo
    assert "Reservé" not in decir("Hola")
    # Pedir OTRA hora ya teniendo una activa: se puede tener más de una cita
    r = decir("Barba completa el martes a las 12, Yohel yohel@gmail.com 967158624")
    assert "Reservé" in r and "2 horas reservadas" in r, r
    # Pedir exactamente la misma no la duplica
    r = decir("Barba completa el martes a las 12, Yohel yohel@gmail.com 967158624")
    assert "Ya tienes reservado" in r, r
    assert len(c.get(f"/negocio/{idn}/agenda", headers=h).json()) == 2

    # Todo en un solo mensaje, otro cliente; hora ocupada -> sugiere horas libres
    r = decir("Quiero Degradado con diseño mañana a las 18, me llamo Ana Soto, ana@correo.cl, +56 9 1234 5678", tel="56912345678")
    assert "Reservé" not in r and ("ocupad" in r.lower() or "libre" in r.lower()), r
    r = decir("a las 19 entonces", tel="56912345678")
    assert "Reservé" in r, r
    # Con menos de 2 h de anticipación
    r = decir("Barba completa hoy a las 17, Pedro pedro@correo.cl 987654321", tel="56987654321")
    assert "2 horas" in r and "Reservé" not in r, r


if __name__ == "__main__":
    test_extractores()
    test_conversacion_real()
    print("OK: agendar sin IA")
