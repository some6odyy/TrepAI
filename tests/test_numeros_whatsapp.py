"""Pruebas del listado de números del WABA, con la Graph API de Meta simulada.
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


def vincular(idn, phone_id, conectado=True):
    db = SessionLocal()
    n = db.get(Negocio, idn)
    n.whatsapp_phone_number_id = phone_id
    n.whatsapp_token = "cifrado" if conectado else None
    n.estado_bot = conectado
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
    assert r["url_administrador"].endswith("waba_id=WABA1")
    propio, ajeno = r["numeros"]
    # el propio está conectado: hay que desvincularlo antes de borrarlo en Meta
    assert propio["vinculado_a"] == "este" and not propio["se_puede_eliminar"]
    assert ajeno["vinculado_a"] == "otro" and not ajeno["se_puede_eliminar"]
    assert "5678" in ajeno["numero"] and "1234" not in ajeno["numero"] and ajeno["nombre"] is None

    # Tras desvincular, el número queda suelto y se puede eliminar en Meta
    c.delete(f"/negocio/{idn}/whatsapp", headers=h)
    with patch("app.routers.negocio.requests.get", return_value=resp(data=NUMEROS_META)):
        propio = c.get(f"/negocio/{idn}/whatsapp/numeros", headers=h).json()["numeros"][0]
    assert propio["vinculado_a"] is None and propio["se_puede_eliminar"]

def test_numero_a_medio_verificar():
    """Se pidió el SMS pero nunca se verificó: no está conectado y se debe poder eliminar."""
    h, idn = cuenta("c@test.cl")
    vincular(idn, "333", conectado=False)
    meta = {"data": [{"id": "333", "display_phone_number": "+56 9 9455 1451", "status": "PENDING"}]}

    with patch("app.routers.negocio.requests.get", return_value=resp(data=meta)):
        n = c.get(f"/negocio/{idn}/whatsapp/numeros", headers=h).json()["numeros"][0]
    assert n["vinculado_a"] == "este_pendiente" and n["se_puede_eliminar"]

    # Ya se borró en Meta: el negocio deja de apuntar a ese número
    with patch("app.routers.negocio.requests.get", return_value=resp(data={"data": []})):
        assert c.get(f"/negocio/{idn}/whatsapp/numeros", headers=h).json()["numeros"] == []
    assert c.get(f"/negocio/{idn}/whatsapp", headers=h).json()["phone_number_id"] is None

def test_verificar_codigo():
    import requests as rq
    h, idn = cuenta("d@test.cl")
    vincular(idn, "444", conectado=False)
    no_verificado = resp(data={"code_verification_status": "NOT_VERIFIED"})
    url = f"/negocio/{idn}/whatsapp/verificar-codigo"

    # Meta no responde: 502 con mensaje legible (antes era un 500)
    with patch("app.routers.negocio.requests.get", return_value=no_verificado), \
        patch("app.routers.negocio.requests.post", side_effect=rq.Timeout("lento")):
        r = c.post(url, headers=h, json={"codigo": "123456"})
    assert r.status_code == 502 and "Meta no respondió" in r.json()["detail"]

    # Código incorrecto: se muestra el motivo que da Meta
    with patch("app.routers.negocio.requests.get", return_value=no_verificado), \
        patch("app.routers.negocio.requests.post",
            return_value=resp(ok=False, status=400, data={"error": {"message": "Code mismatch", "code": 136025}})):
        r = c.post(url, headers=h, json={"codigo": "000000"})
    assert r.status_code == 400 and "Code mismatch" in r.json()["detail"]

    # El intento anterior se cortó pero Meta ya lo verificó: se salta verify_code y se activa
    with patch("app.routers.negocio.requests.get",
            return_value=resp(data={"code_verification_status": "VERIFIED"})), \
        patch("app.routers.negocio.requests.post", return_value=resp(data={"success": True})) as post:
        r = c.post(url, headers=h, json={"codigo": "123456"})
    assert r.status_code == 200 and r.json()["conectado"] is True
    urls = [llamada[0][0] for llamada in post.call_args_list]
    assert len(urls) == 1 and urls[0].endswith("/444/register")