"""Un cliente puede tener VARIAS citas activas y cambiar cualquiera de ellas
por WhatsApp, siempre que la nueva hora no choque con otras reservas (de
otros clientes o suyas). Con IA (marcadores) y sin IA (texto libre).
    python -m pytest tests/test_varias_citas.py
"""
import os, tempfile, uuid
from datetime import datetime

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.negocio import Negocio  # noqa: E402
from app.services import ai_service, chat_service, agendado_ia  # noqa: E402

c = TestClient(app)
AHORA = datetime(2026, 11, 24, 10, 0)  # martes
DATOS = "|Yohel Faundez|yohel@gmail.com|56967158624"
TEL = "56967158624"


def preparar(correo):
    c.post("/auth/registro", json={"nombre": "Test", "correo": correo, "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": correo, "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", json={"nombre_negocio": "Barbería", "horario": "Lun-Sáb 10:00-20:00"}, headers=h).json()["id_negocio"]
    for n, d in (("Degradado", 45), ("Perfilado de barba", 30)):
        c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": n, "precio": 10000, "duracion_estimada": d})
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    return h, idn


def enviar(idn, texto_ia, mensaje="hola", tel=TEL):
    """texto_ia=None simula que la IA no está disponible (modo sin IA)."""
    ai_service.generar_respuesta_o_none = lambda prompt, provider=None, model=None: texto_ia
    ai_service.ahora_chile = lambda: AHORA
    db = SessionLocal()
    try:
        return chat_service.procesar_mensaje(db, db.get(Negocio, idn), tel, mensaje)
    finally:
        db.close()


def activas(h, idn):
    return sorted(
        (x for x in c.get(f"/negocio/{idn}/agenda", headers=h).json() if x["estado_cita"] in ("pendiente", "confirmada")),
        key=lambda x: (x["fecha_cita"], x["hora_cita"]),
    )


def test_marcadores_con_id():
    assert agendado_ia.extraer_solicitud("[[REAGENDAR|Degradado|2026-11-27|15:00|#12]]")[1].id_cita == 12
    assert agendado_ia.extraer_solicitud("[[REAGENDAR|Degradado|2026-11-27|15:00]]")[1].id_cita is None
    assert agendado_ia.extraer_solicitud("[[REAGENDAR|2026-11-27|15:00|7]]")[1].id_cita == 7
    assert agendado_ia.extraer_solicitud("[[CANCELAR|#3]]")[1].id_cita == 3
    assert agendado_ia.extraer_solicitud("[[CANCELAR]]")[1].id_cita is None


def test_varias_citas_con_ia():
    h, idn = preparar("varias@t.cl")
    assert "Reservé" in enviar(idn, "[[AGENDAR|Degradado|2026-11-27|14:00" + DATOS + "]]")
    # Segunda cita sin repetir los datos: se usan los de la cita anterior
    r = enviar(idn, "[[AGENDAR|Perfilado de barba|2026-11-28|11:00]]")
    assert "Reservé" in r and "2 horas reservadas" in r, r
    citas = activas(h, idn)
    assert len(citas) == 2 and citas[1]["correo_cliente"] == "yohel@gmail.com"
    id_degradado, id_barba = citas[0]["id_agenda"], citas[1]["id_agenda"]

    # La IA ve las dos citas con su #id
    capturado = {}
    def ia(prompt, provider=None, model=None):
        capturado["p"] = prompt
        return "ok"
    ai_service.generar_respuesta_o_none = ia
    db = SessionLocal()
    chat_service.procesar_mensaje(db, db.get(Negocio, idn), TEL, "qué horas tengo?")
    db.close()
    assert f"#{id_degradado}: Degradado" in capturado["p"] and f"#{id_barba}: Perfilado de barba" in capturado["p"]

    # Una cita no puede cruzarse con otra del mismo cliente
    r = enviar(idn, "[[AGENDAR|Perfilado de barba|2026-11-27|14:30]]")
    assert "choca con tu otra cita" in r, r

    # Cambiar SOLO la barba (por id): el degradado sigue igual
    r = enviar(idn, f"[[REAGENDAR|Perfilado de barba|2026-11-28|16:00|#{id_barba}]]")
    assert "Cambié tu hora" in r and "otra hora reservada sigue igual" in r, r
    citas = activas(h, idn)
    assert len(citas) == 2
    assert citas[0]["id_agenda"] == id_degradado and citas[0]["hora_cita"].startswith("14:00")
    assert citas[1]["id_agenda"] == id_barba and citas[1]["hora_cita"].startswith("16:00")

    # Mover el degradado encima de su propia barba: no se permite
    r = enviar(idn, f"[[REAGENDAR|Degradado|2026-11-28|15:45|#{id_degradado}]]")
    assert "choca con tu otra cita" in r, r

    # Mover el degradado a una hora tomada por OTRO cliente: sugiere libres
    enviar(idn, "[[AGENDAR|Degradado|2026-11-27|17:00|Ana|ana@gmail.com|56912345678]]", tel="56912345678")
    r = enviar(idn, f"[[REAGENDAR|Degradado|2026-11-27|17:00|#{id_degradado}]]")
    assert "ya están tomadas" in r, r

    # Sin #id y con dos citas de distinto servicio: el servicio basta para saber cuál
    r = enviar(idn, "[[REAGENDAR|Degradado|2026-11-27|12:00]]")
    assert "Cambié tu hora de *Degradado*" in r, r
    # Sin servicio ni id: pregunta cuál
    r = enviar(idn, "[[REAGENDAR|2026-11-27|13:00]]")
    assert "¿Cuál quieres cambiar?" in r and "1." in r and "2." in r, r
    r = enviar(idn, "[[CANCELAR]]")
    assert "¿Cuál quieres cancelar?" in r, r

    # Cancelar solo una
    r = enviar(idn, f"[[CANCELAR|#{id_barba}]]")
    assert "cancelé tu cita de *Perfilado de barba*" in r, r
    citas = activas(h, idn)
    assert [x["id_agenda"] for x in citas if x["telefono_contacto"] == TEL] == [id_degradado]


def test_varias_citas_sin_ia():
    h, idn = preparar("sinia-varias@t.cl")
    assert "Reservé" in enviar(idn, None, "Perfilado de barba el lunes 30-11 a las 18, Yohel yohel@gmail.com 967158624")

    # Caso de la captura: pide OTRA hora teniendo una. Ya no se confunde con
    # "cambiar datos de contacto": se reservan con los datos de su otra hora.
    r = enviar(idn, None, "Quiero un Degradado")
    r = enviar(idn, None, "Viernes 14 hrs")
    assert "Reservé *Degradado*" in r and "2 horas reservadas" in r and "mismos datos" in r, r
    assert len(activas(h, idn)) == 2
    # Si manda datos nuevos mientras arma otra reserva, son para esa reserva
    enviar(idn, None, "Perfilado de barba")
    r = enviar(idn, None, "Ana Pérez\nana@gmail.com\n912345678")
    assert "Actualicé los datos" not in r, r

    # Cambiar una por texto libre, nombrando el servicio
    r = enviar(idn, None, "cambia el degradado al sábado a las 15")
    assert "Cambié tu hora de *Degradado*" in r and "sábado 28-11" in r, r
    # Sin decir cuál: pregunta, y se responde con el número de la lista
    r = enviar(idn, None, "quiero cambiar mi hora a las 12")
    assert "¿Cuál quieres cambiar?" in r, r
    r = enviar(idn, None, "quiero cambiar mi hora a las 12")
    assert "¿Cuál quieres cambiar?" in r, r
    r = enviar(idn, None, "2")
    assert "Cambié tu hora" in r and "12:00" in r, r
    citas = activas(h, idn)
    assert len(citas) == 2 and any(x["hora_cita"].startswith("12:00") for x in citas)

    # Cancelar una por texto
    r = enviar(idn, None, "cancela la barba")
    assert "cancelé tu cita de *Perfilado de barba*" in r, r
    assert len(activas(h, idn)) == 1


def test_cita_pasada_no_cuenta_como_activa():
    h, idn = preparar("pasada@t.cl")
    global AHORA
    original = AHORA
    AHORA = datetime(2026, 11, 23, 10, 0)
    assert "Reservé" in enviar(idn, "[[AGENDAR|Degradado|2026-11-23|18:00" + DATOS + "]]")
    AHORA = original  # ya es martes 24: la del lunes pasó
    r = enviar(idn, "[[CANCELAR]]")
    assert "No tienes ninguna cita activa" in r, r
    AHORA = original


def test_cancelar_horas():
    from app.services import recordatorios, email_service, notificacion_service
    enviados = []
    email_service.enviar_correo = lambda destino, asunto, *a, **k: enviados.append(("correo", destino, asunto)) or True
    notificacion_service.enviar_whatsapp_cloud = lambda neg, tel, texto: enviados.append(("wsp", tel, texto)) or True
    notificacion_service.enviar_plantilla_cloud = lambda *a, **k: True

    h, idn = preparar("cancelar@t.cl")
    enviar(idn, "[[AGENDAR|Degradado|2026-11-27|14:00" + DATOS + "]]")
    enviar(idn, "[[AGENDAR|Perfilado de barba|2026-11-28|11:00]]")
    enviar(idn, "[[AGENDAR|Degradado|2026-11-30|12:00]]")

    # Con el agendado automático APAGADO igual se puede cancelar (IA y sin IA)
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": False})
    capturado = {}
    def ia(prompt, provider=None, model=None):
        capturado["p"] = prompt
        return "[[CANCELAR|TODAS]]" if "todas" in prompt.lower().split("nuevo mensaje del cliente:")[-1] else "ok"
    ai_service.generar_respuesta_o_none = ia
    citas = activas(h, idn)
    r = enviar(idn, f"[[CANCELAR|#{citas[2]['id_agenda']}]]", "cancela la del lunes")
    assert "cancelé tu cita de *Degradado* del lunes 30-11" in r and "Sigues teniendo" in r, r
    r = enviar(idn, None, "cancela la barba")
    assert "cancelé tu cita de *Perfilado de barba*" in r, r
    assert len(activas(h, idn)) == 1

    # El negocio recibe un aviso por cada cancelación
    avisos = c.get(f"/negocio/{idn}/novedades", headers=h).json()
    textos = str(avisos)
    assert textos.count("canceló por WhatsApp") >= 2, textos

    # Cancelar TODAS
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    enviar(idn, "[[AGENDAR|Perfilado de barba|2026-11-28|11:00]]")
    assert len(activas(h, idn)) == 2
    r = enviar(idn, "[[CANCELAR|TODAS]]", "cancela todas mis horas")
    assert "cancelé tus 2 horas" in r, r
    assert activas(h, idn) == []
    r = enviar(idn, None, "cancela mi hora")
    assert "No tienes ninguna cita activa" in r, r

    # Sin IA: "cancela todas"
    enviar(idn, "[[AGENDAR|Degradado|2026-11-27|14:00" + DATOS + "]]")
    enviar(idn, "[[AGENDAR|Perfilado de barba|2026-11-28|11:00]]")
    r = enviar(idn, None, "cancela todas mis horas")
    assert "cancelé tus 2 horas" in r, r

    # El negocio cancela desde la Agenda: se le avisa al cliente (correo + WhatsApp)
    enviar(idn, "[[AGENDAR|Degradado|2026-11-27|16:00" + DATOS + "]]")
    cita = activas(h, idn)[0]
    enviados.clear()
    r = c.patch(f"/negocio/{idn}/agenda/{cita['id_agenda']}/estado", headers=h,
                json={"estado_cita": "cancelada", "motivo": "el barbero está enfermo"})
    assert r.status_code == 200 and r.json()["estado_cita"] == "cancelada"
    assert any(t == "correo" and "Hora cancelada" in asunto for t, _, asunto in enviados), enviados
    assert any(t == "wsp" and "el barbero está enfermo" in txt for t, _, txt in enviados), enviados
    # Sin aviso si el dueño lo desmarca
    enviar(idn, "[[AGENDAR|Degradado|2026-11-27|17:00" + DATOS + "]]")
    cita = activas(h, idn)[0]
    enviados.clear()
    c.patch(f"/negocio/{idn}/agenda/{cita['id_agenda']}/estado", headers=h,
            json={"estado_cita": "cancelada", "avisar_cliente": False})
    assert enviados == []
