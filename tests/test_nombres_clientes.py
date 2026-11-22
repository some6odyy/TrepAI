"""El listado de Conversaciones muestra el nombre del cliente:
1) nombre de perfil de WhatsApp (contacts[0].profile.name del webhook),
2) el nombre que da al agendar lo reemplaza,
3) clientes antiguos sin nombre usan el de su última cita.
    PYTHONPATH=. python tests/test_nombres_clientes.py
"""
import os, tempfile, uuid
from datetime import timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.tiempo import ahora_chile_naive  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.cliente import Cliente  # noqa: E402
from app.services import whatsapp_service, notificacion_service, email_service  # noqa: E402

whatsapp_service.enviar_mensaje = lambda **k: True
notificacion_service.enviar_whatsapp_cloud = lambda *a: True
email_service.enviar_correo = lambda *a, **k: True
c = TestClient(app)


def evento(tel, texto, nombre=None):
    value = {"metadata": {"phone_number_id": "PNID-NOMBRES"},
             "messages": [{"from": tel, "type": "text", "text": {"body": texto}}]}
    if nombre is not None:
        value["contacts"] = [{"profile": {"name": nombre}, "wa_id": tel}]
    return {"entry": [{"changes": [{"value": value}]}]}


def nombres(h, idn):
    return {x["telefono_cliente"]: x["nombre_cliente"] for x in c.get(f"/negocio/{idn}/conversaciones", headers=h).json()}


def test_nombres():
    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "nombres@test.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "nombres@test.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería N", "horario": "Lun-Dom 00:00-23:59"}).json()["id_negocio"]
    serv = c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
                  json={"nombre_servicio": "Corte", "precio": 5000, "duracion_estimada": 30}).json()["id_servicio"]
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": "PNID-NOMBRES", "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=false", headers=h)

    # 1) nombre de perfil de WhatsApp
    c.post("/webhook", json=evento("56911110001", "hola", "  Camila   Rojas "))
    c.post("/webhook", json=evento("56911110002", "hola"))  # sin perfil
    n = nombres(h, idn)
    assert n["56911110001"] == "Camila Rojas" and n["56911110002"] is None, n
    # un mensaje posterior con otro nombre de perfil no pisa el que ya tenemos
    c.post("/webhook", json=evento("56911110001", "?", "Cami 💅"))
    assert nombres(h, idn)["56911110001"] == "Camila Rojas"

    # 2) el nombre dado al agendar reemplaza al de perfil
    inicio = (ahora_chile_naive() + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={
        "telefono_cliente": "+56911110001", "nombre_cliente": "Camila Andrea Rojas", "correo_cliente": "cami@test.cl",
        "id_servicio": serv, "fecha_cita": inicio.date().isoformat(), "hora_cita": "12:00:00"})
    assert r.status_code == 201, r.text
    assert nombres(h, idn)["56911110001"] == "Camila Andrea Rojas"

    # 3) cliente antiguo sin nombre guardado: se usa el de su cita
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={
        "telefono_cliente": "+56911110002", "nombre_cliente": "Pedro Soto", "correo_cliente": "pedro@test.cl",
        "id_servicio": serv, "fecha_cita": inicio.date().isoformat(), "hora_cita": "13:00:00"})
    assert r.status_code == 201, r.text
    db = SessionLocal()
    db.query(Cliente).filter(Cliente.telefono == "56911110002").update({"nombre_cliente": None})
    db.commit(); db.close()
    assert nombres(h, idn)["56911110002"] == "Pedro Soto"

    assert whatsapp_service.nombre_perfil({}) is None


if __name__ == "__main__":
    test_nombres()
    print("OK: nombres de clientes en Conversaciones")
