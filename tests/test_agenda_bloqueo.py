"""Pruebas del bloqueo de horarios y la expiración a 2 horas.
Usa SQLite en memoria-archivo temporal; no necesita el servidor corriendo.
    python -m pytest tests/test_agenda_bloqueo.py   (o: python tests/test_agenda_bloqueo.py)
"""
import os, tempfile, uuid
from datetime import timedelta

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.agenda import Agenda  # noqa: E402
from app.services.agenda_service import ahora_utc  # noqa: E402

c = TestClient(app)


def preparar():
    c.post("/auth/registro", json={"nombre": "Test", "correo": "t@t.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "t@t.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", json={"nombre_negocio": "Silvabarber"}, headers=h).json()["id_negocio"]
    corto = c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
                   json={"nombre_servicio": "Corte", "precio": 8000, "duracion_estimada": 30}).json()["id_servicio"]
    largo = c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
                   json={"nombre_servicio": "Corte+barba", "precio": 12000, "duracion_estimada": 60}).json()["id_servicio"]
    return h, idn, corto, largo


def cita(h, idn, serv, hora, tel="+56911111111"):
    return c.post(f"/negocio/{idn}/agenda", headers=h, json={
        "telefono_cliente": tel, "id_servicio": serv, "fecha_cita": "2026-12-01", "hora_cita": hora})


def test_todo():
    h, idn, corto, largo = preparar()

    r1 = cita(h, idn, corto, "10:00:00"); assert r1.status_code == 201, r1.text
    # mismo horario, y a mitad del bloque de 30 min -> 409
    assert cita(h, idn, corto, "10:00:00", "+56922222222").status_code == 409
    assert cita(h, idn, corto, "10:15:00", "+56922222222").status_code == 409
    # justo al terminar (10:30) -> permitido
    assert cita(h, idn, corto, "10:30:00", "+56922222222").status_code == 201
    # servicio de 60 min que arranca 09:45 pisaría 10:00 -> 409; a las 09:30 termina 10:30 y pisa la de 10:30 -> 409
    assert cita(h, idn, largo, "09:45:00", "+56933333333").status_code == 409
    assert cita(h, idn, largo, "09:30:00", "+56933333333").status_code == 409
    assert cita(h, idn, largo, "08:00:00", "+56933333333").status_code == 201

    # cancelar libera el horario
    id1 = r1.json()["id_agenda"]
    assert c.patch(f"/negocio/{idn}/agenda/{id1}/estado", headers=h, json={"estado_cita": "cancelada"}).status_code == 200
    r = cita(h, idn, corto, "10:00:00", "+56944444444"); assert r.status_code == 201
    # reactivar la cancelada ahora choca
    assert c.patch(f"/negocio/{idn}/agenda/{id1}/estado", headers=h, json={"estado_cita": "confirmada"}).status_code == 409

    # expiración: la pendiente con >2h se libera, la confirmada NO
    id_nueva = r.json()["id_agenda"]
    db = SessionLocal()
    db.query(Agenda).filter(Agenda.id_agenda == id_nueva).update({"fecha_creacion": ahora_utc() - timedelta(hours=2, minutes=1)})
    db.commit(); db.close()
    lista = c.get(f"/negocio/{idn}/agenda", headers=h).json()
    assert next(x for x in lista if x["id_agenda"] == id_nueva)["estado_cita"] == "expirada"
    assert cita(h, idn, corto, "10:00:00", "+56955555555").status_code == 201  # liberado

    id_conf = next(x for x in lista if x["hora_cita"].startswith("10:30"))["id_agenda"]
    c.patch(f"/negocio/{idn}/agenda/{id_conf}/estado", headers=h, json={"estado_cita": "confirmada"})
    db = SessionLocal()
    db.query(Agenda).filter(Agenda.id_agenda == id_conf).update({"fecha_creacion": ahora_utc() - timedelta(hours=5)})
    db.commit(); db.close()
    c.get(f"/negocio/{idn}/agenda", headers=h)
    assert cita(h, idn, corto, "10:30:00", "+56966666666").status_code == 409  # confirmada sigue bloqueando
    print("OK: todas las pruebas de agenda pasaron")


if __name__ == "__main__":
    test_todo()
