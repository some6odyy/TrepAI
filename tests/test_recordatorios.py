"""Pruebas de: anticipación mínima de 2 h, datos de contacto obligatorios,
recordatorios (al agendar, 24 h antes, confirmación 1 h antes), respuesta
SÍ/NO del cliente (WhatsApp y enlace del correo), responder desde
Conversaciones, novedades en vivo y el endpoint del cron.

No envía nada real: WhatsApp y correo se reemplazan por funciones que
registran lo que se habría enviado.
    PYTHONPATH=. python tests/test_recordatorios.py
"""
import os, re, tempfile, uuid
from datetime import date, datetime, time, timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core import config  # noqa: E402
from app.core.tiempo import ahora_chile_naive, ahora_utc  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.agenda import Agenda  # noqa: E402
from app.models.conversacion import Mensaje  # noqa: E402
from app.models.negocio import Negocio  # noqa: E402
from app.services import email_service, notificacion_service, recordatorios, whatsapp_service  # noqa: E402

c = TestClient(app)
ENVIADOS = []  # (canal, destino, texto)

notificacion_service.enviar_whatsapp_cloud = lambda n, tel, txt: ENVIADOS.append(("wa", tel, txt)) or True
notificacion_service.enviar_plantilla_cloud = lambda n, tel, pl, idi, params: ENVIADOS.append(("plantilla", tel, pl)) or True
whatsapp_service.enviar_mensaje = lambda telefono_destino, texto, phone_number_id, token: ENVIADOS.append(("wa_resp", telefono_destino, texto)) or True
recordatorios.email_service.enviar_correo = lambda dest, asunto, txt, nombre="TrepAI": ENVIADOS.append(("correo", dest, asunto + "\n" + txt)) or True

TEL_CHAT = "56955550000"


def canales(destino=None):
    return [(k, d) for k, d, _ in ENVIADOS if destino is None or d == destino]


def preparar():
    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "duenio@test.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "duenio@test.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Silvabarber", "horario": "Lun-Dom 00:00-23:59"}).json()["id_negocio"]
    serv = c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
                  json={"nombre_servicio": "Degradado", "precio": 8000, "duracion_estimada": 30}).json()["id_servicio"]
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": "PNID-TEST-1", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)
    return h, idn, serv


def evento(texto, tel=TEL_CHAT):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID-TEST-1"},
        "messages": [{"from": tel, "type": "text", "text": {"body": texto}}],
    }}]}]}


def crear(h, idn, serv, inicio: datetime, tel="+56 9 5555 0000", correo="juan@correo.cl", nombre="Juan Pérez"):
    return c.post(f"/negocio/{idn}/agenda", headers=h, json={
        "telefono_cliente": tel, "nombre_cliente": nombre, "correo_cliente": correo,
        "id_servicio": serv, "fecha_cita": inicio.date().isoformat(), "hora_cita": inicio.time().strftime("%H:%M:%S"),
    })


def redondear(dt: datetime) -> datetime:
    r = dt.replace(second=0, microsecond=0) + timedelta(minutes=(-dt.minute) % 30)
    # El horario de prueba es 00:00-23:59: una cita de 30 min a las 23:30
    # terminaría pasada la medianoche, así que se corre a las 00:00.
    if r.hour == 23 and r.minute == 30:
        r += timedelta(minutes=30)
    return r


def test_todo():
    h, idn, serv = preparar()
    ahora = ahora_chile_naive()

    # ---- Anticipación mínima y datos obligatorios (agenda manual) ----
    r = crear(h, idn, serv, ahora + timedelta(minutes=90))
    assert r.status_code == 422 and "2 horas" in r.json()["detail"], r.text
    r = crear(h, idn, serv, redondear(ahora + timedelta(days=3)), correo="malo")
    assert r.status_code == 422 and "correo" in r.text, r.text
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={
        "telefono_cliente": "+56955550000", "id_servicio": serv, "fecha_cita": "2030-01-01", "hora_cita": "10:00:00"})
    assert r.status_code == 422  # falta nombre y correo

    # ---- Aviso al agendar ----
    inicio = redondear(ahora + timedelta(days=3))
    r = crear(h, idn, serv, inicio)
    assert r.status_code == 201, r.text
    cita = r.json()
    assert cita["correo_cliente"] == "juan@correo.cl" and cita["telefono_contacto"] == TEL_CHAT
    # El cliente nunca escribió -> sin ventana de 24 h ni plantilla: solo correo
    assert canales() == [("correo", "juan@correo.cl")], ENVIADOS
    ENVIADOS.clear()

    db = SessionLocal()
    negocio = db.get(Negocio, idn)

    # Ejecutar de nuevo no duplica nada
    assert recordatorios.ejecutar_pendientes(db, idn)["enviados"] == 0

    # ---- Recordatorio 24 h antes (con plantilla configurada) ----
    config.WHATSAPP_PLANTILLA_RECORDATORIO = "trepai_recordatorio"
    config.WHATSAPP_PLANTILLA_CONFIRMACION = "trepai_confirmacion"
    enviados = recordatorios.procesar_cita(db, negocio, cita["id_agenda"], inicio - timedelta(hours=20))
    assert enviados == ["recordatorio_24h"], enviados
    assert ("plantilla", TEL_CHAT) in canales() and ("correo", "juan@correo.cl") in canales(), ENVIADOS
    assert recordatorios.procesar_cita(db, negocio, cita["id_agenda"], inicio - timedelta(hours=19)) == []
    ENVIADOS.clear()

    # ---- El cliente escribe (abre la ventana de 24 h) y el negocio responde desde Conversaciones ----
    r = c.post("/webhook", json=evento("hola, a qué hora cierran?"))
    assert r.status_code == 200
    convs = c.get(f"/negocio/{idn}/conversaciones", headers=h).json()
    assert len(convs) == 1 and convs[0]["puede_responder"] is True
    id_conv = convs[0]["id_conversacion"]
    ENVIADOS.clear()
    r = c.post(f"/negocio/{idn}/conversaciones/{id_conv}/mensajes", headers=h, json={"texto": "Hola Juan, cerramos a las 20:00"})
    assert r.status_code == 201 and r.json()["emisor"] == "negocio", r.text
    assert ("wa_resp", TEL_CHAT) in canales()
    msgs = c.get(f"/negocio/{idn}/conversaciones/{id_conv}/mensajes", headers=h).json()
    assert msgs[-1]["emisor"] == "negocio" and "20:00" in msgs[-1]["contenido"]
    ENVIADOS.clear()

    # ---- Confirmación 1 h antes: ventana abierta -> texto libre + aviso al negocio ----
    enviados = recordatorios.procesar_cita(db, negocio, cita["id_agenda"], inicio - timedelta(minutes=55))
    assert enviados == ["confirmacion_1h"], enviados
    textos = [t for k, d, t in ENVIADOS if k == "wa"]
    assert textos and "Responde *SÍ*" in textos[0], ENVIADOS
    correo_cliente = [t for k, d, t in ENVIADOS if k == "correo" and d == "juan@correo.cl"][0]
    assert "/cita/respuesta?t=" in correo_cliente
    assert ("correo", "duenio@test.cl") in canales()  # aviso al administrador
    nov = c.get(f"/negocio/{idn}/novedades", headers=h).json()
    assert any(a["tipo"] == "cita_1h" for a in nov["avisos"]), nov
    ENVIADOS.clear()

    # ---- El cliente responde "Sí" por WhatsApp -> confirmada sin pasar por la IA ----
    firma_antes = nov["firma_agenda"]
    r = c.post("/webhook", json=evento("Sí, voy!"))
    assert r.json()["status"] == "procesado", r.json()
    assert any(k == "wa_resp" and "confirmada" in t for k, d, t in ENVIADOS), ENVIADOS
    cita_db = db.get(Agenda, cita["id_agenda"]); db.refresh(cita_db)
    assert cita_db.estado_cita == "confirmada" and cita_db.respuesta_cliente == "si"
    nov = c.get(f"/negocio/{idn}/novedades", headers=h).json()
    assert nov["firma_agenda"] != firma_antes
    assert any(a["tipo"] == "cliente_confirmo" for a in nov["avisos"])
    assert c.post(f"/negocio/{idn}/avisos/leer", headers=h, json={}).json()["marcados"] >= 2
    assert c.get(f"/negocio/{idn}/novedades", headers=h).json()["avisos"] == []

    # ---- Segunda cita: el cliente cancela con el enlace del correo ----
    ENVIADOS.clear()
    inicio2 = redondear(ahora + timedelta(hours=5))
    r = crear(h, idn, serv, inicio2, tel="+56966660000", correo="ana@correo.cl", nombre="Ana")
    assert r.status_code == 201, r.text
    id2 = r.json()["id_agenda"]
    # Agendada con < 24 h: el recordatorio de 24 h se omite (ya llegó el aviso de agendado)
    assert recordatorios.procesar_cita(db, negocio, id2, inicio2 - timedelta(hours=3)) == []
    assert recordatorios.procesar_cita(db, negocio, id2, inicio2 - timedelta(minutes=50)) == ["confirmacion_1h"]
    # Ana nunca escribió: WhatsApp por plantilla
    assert ("plantilla", "56966660000") in canales(), ENVIADOS
    correo_ana = [t for k, d, t in ENVIADOS if k == "correo" and d == "ana@correo.cl"][-1]
    link_no = re.search(r"(/cita/respuesta\?t=\S+&r=no)", correo_ana).group(1)
    pagina = c.get(link_no)
    assert pagina.status_code == 200 and "cancelada" in pagina.text.lower(), pagina.text
    cita2 = db.get(Agenda, id2); db.refresh(cita2)
    assert cita2.estado_cita == "cancelada" and cita2.respuesta_cliente == "no"
    assert "Ya registramos" in c.get(link_no).text  # el enlace no se usa dos veces
    assert "no válido" in c.get("/cita/respuesta?t=falso.123&r=si").text

    # ---- Responder fuera de la ventana de 24 h -> 409 ----
    conv_ana = [x for x in c.get(f"/negocio/{idn}/conversaciones", headers=h).json() if x["telefono_cliente"] == "56966660000"]
    assert conv_ana and conv_ana[0]["puede_responder"] is False
    r = c.post(f"/negocio/{idn}/conversaciones/{conv_ana[0]['id_conversacion']}/mensajes", headers=h, json={"texto": "hola"})
    assert r.status_code == 409, r.text

    # ---- Con el bot pausado, el mensaje queda en Conversaciones (para responder a mano) ----
    c.patch(f"/negocio/{idn}/estado-bot?activo=false", headers=h)
    total = db.query(Mensaje).count()
    assert c.post("/webhook", json=evento("¿tienen hora mañana?")).json()["status"] == "bot_inactivo"
    assert db.query(Mensaje).count() == total + 1

    # ---- Cron ----
    config.CRON_SECRET = ""
    assert c.post("/tareas/recordatorios").status_code == 404
    config.CRON_SECRET = "secreto-de-prueba"
    assert c.post("/tareas/recordatorios", headers={"Authorization": "Bearer otro"}).status_code == 401
    r = c.get("/tareas/recordatorios", headers={"Authorization": "Bearer secreto-de-prueba"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text

    # ---- Interpretación de respuestas ----
    assert recordatorios.interpretar_respuesta("SI") == "si"
    assert recordatorios.interpretar_respuesta("👍") == "si"
    assert recordatorios.interpretar_respuesta("no puedo, se me complicó") == "no"
    assert recordatorios.interpretar_respuesta("¿dónde queda el local?") is None
    db.close()


if __name__ == "__main__":
    test_todo()
    print("OK: recordatorios, confirmaciones, respuestas y novedades")
