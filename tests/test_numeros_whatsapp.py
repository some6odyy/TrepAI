"""Pruebas de listar/eliminar números del WABA, con la Graph API de Meta simulada.
    python -m pytest tests/test_numeros_whatsapp.py
"""
import os, tempfile, uuid
from unittest.mock import patch, MagicMock

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"
os.environ["META_WABA_ID"] = "WABA1"
os.environ["META_MASTER_TOKEN"] = "token-maestro"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.negocio import Negocio  # noqa: E402

c = TestClient(app)

NUMEROS_META = {"data": [
    {"id": "111", "display_phone_number": "+56 9 9495 1451", "verified_name": "Barbería", "status": "CONNECTED"},
    {"id": "222", "display_phone_number": "+56 9 1234 5678", "verified_name": "Otra", "status": "PENDING"},
]}


def resp(ok=True, data=None, status=200):
    r = MagicMock(ok=ok, status_code=status)
    r.json.return_value = data or {}
    return r


def cuenta(correo):
    c.post("/auth/registro", json={"nombre": "Test", "correo": correo, "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": correo, "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", json={"nombre_negocio": "Barberia Test"}, headers=h).json()["id_negocio"]
    return h, idn


def vincular(idn, phone_id):
    db = SessionLocal()
    n = db.get(Negocio, idn)
    n.whatsapp_phone_number_id = phone_id
    n.estado_bot = True
    db.commit()
    db.close()


def test_listar_y_eliminar():
    h, idn = cuenta("a@test.cl")
    _, otro = cuenta("b@test.cl")
    vincular(idn, "111")
    vincular(otro, "222")

    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)):
        r = c.get(f"/negocio/{idn}/whatsapp/numeros", headers=h).json()
    assert r["limite"] == 2 and len(r["numeros"]) == 2
    propio, ajeno = r["numeros"]
    assert propio["vinculado_a"] == "este" and propio["se_puede_eliminar"]
    assert ajeno["vinculado_a"] == "otro" and not ajeno["se_puede_eliminar"]
    assert "5678" in ajeno["numero"] and "1234" not in ajeno["numero"] and ajeno["nombre"] is None

    # No se puede borrar el número de otro negocio
    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)), \
         patch("app.routers.negocio.requests.delete") as borrar:
        r = c.delete(f"/negocio/{idn}/whatsapp/numeros/222", headers=h)
    assert r.status_code == 409 and not borrar.called

    # Número que no está en el WABA
    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)):
        assert c.delete(f"/negocio/{idn}/whatsapp/numeros/999", headers=h).status_code == 404

    # Meta rechaza el borrado: se muestra su motivo
    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)), \
         patch("app.routers.negocio.requests.post", return_value=resp()), \
         patch("app.routers.negocio.requests.delete",
               return_value=resp(ok=False, status=400, data={"error": {"message": "No se puede", "code": 100}})):
        r = c.delete(f"/negocio/{idn}/whatsapp/numeros/111", headers=h)
    assert r.status_code == 400 and "No se puede" in r.json()["detail"]

    # Borrado correcto del propio: también lo desvincula
    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)), \
         patch("app.routers.negocio.requests.post", return_value=resp()) as post, \
         patch("app.routers.negocio.requests.delete", return_value=resp(data={"success": True})) as borrar:
        r = c.delete(f"/negocio/{idn}/whatsapp/numeros/111", headers=h)
    assert r.status_code == 200 and r.json()["desvinculado_de_este_negocio"]
    assert post.call_args[0][0].endswith("/111/deregister")
    assert borrar.call_args[0][0].endswith("/111")
    assert c.get(f"/negocio/{idn}/whatsapp", headers=h).json()["conectado"] is False
