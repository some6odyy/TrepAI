"""Pestaña "Casos de uso": simulador de WhatsApp, prompt dinámico y estado.
    python -m pytest tests/test_demo_casos_uso.py
"""
import os, tempfile, uuid
from datetime import datetime

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_service  # noqa: E402

c = TestClient(app)


def _cuenta(correo):
    c.post("/auth/registro", json={"nombre": "Demo", "correo": correo, "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": correo, "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Demo", "horario": "Lun-Sáb 10:00-20:00"}).json()["id_negocio"]
    return h, idn


def test_simulador():
    ai_service.ahora_chile = lambda: datetime(2026, 11, 24, 10, 0)
    h, idn = _cuenta("demo@t.cl")
    c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": "Degradado", "precio": 12000, "duracion_estimada": 45})
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    url = f"/negocio/{idn}/demo"

    # Solo números de prueba
    assert c.post(f"{url}/mensaje", headers=h, json={"telefono": "56967158624", "texto": "hola"}).status_code == 400

    # Bot apagado: no responde, pero el mensaje queda guardado (RF-04 / RF-05)
    r = c.post(f"{url}/mensaje", headers=h, json={"texto": "hola"}).json()
    assert r["estado"] == "bot_inactivo" and r["respuesta"] is None

    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)
    ai_service.generar_respuesta_o_none = lambda *a, **k: "[[AGENDAR|Degradado|2026-11-27|14:00|Ana|ana@gmail.com|56900000001]]"
    r = c.post(f"{url}/mensaje", headers=h, json={"texto": "Degradado viernes 14"}).json()
    assert r["origen"] == "ia" and "Reservé" in r["respuesta"] and len(r["citas_activas"]) == 1, r
    ai_service.generar_respuesta_o_none = lambda *a, **k: "[[AGENDAR|Degradado|2026-11-28|11:00]]"
    r = c.post(f"{url}/mensaje", headers=h, json={"texto": "y otro el sábado a las 11"}).json()
    assert len(r["citas_activas"]) == 2, r

    p = c.post(f"{url}/prompt", headers=h, json={"texto": "qué horas tengo?"}).json()
    assert "Degradado" in p["prompt"] and "qué horas tengo?" in p["prompt"]
    assert len(c.get(f"{url}/conversacion", headers=h).json()) >= 4

    e = c.get(f"{url}/estado", headers=h).json()
    assert e["bot_activo"] and e["agenda"]["clientes_con_varias_citas"] == 1 and e["historial"]["respuestas_ia"] == 2

    r = c.post(f"{url}/reiniciar", headers=h, json={}).json()
    assert r["citas_canceladas"] == 2
    assert c.get(f"{url}/citas", headers=h).json() == []

    # Multitenant: otro administrador no puede usar la demo de este negocio
    h2, idn2 = _cuenta("otro@t.cl")
    assert c.get(f"{url}/estado", headers=h2).status_code == 403
    assert c.get(f"/negocio/{idn2}/demo/estado", headers=h2).json()["id_otro_negocio"] is not None
