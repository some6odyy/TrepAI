"""Pruebas del agendado automático desde el chat (marcador [[AGENDAR|...]]).
No llama a la IA real: se simula su respuesta.
    python -m pytest tests/test_agendado_ia.py   (o: python tests/test_agendado_ia.py)
"""
import os, tempfile, uuid
from datetime import datetime

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.negocio import Negocio  # noqa: E402
from app.models.agenda import Agenda  # noqa: E402
from app.services import ai_service, chat_service, agendado_ia  # noqa: E402

c = TestClient(app)
AHORA = datetime(2026, 11, 10, 10, 0)  # martes


def preparar():
    c.post("/auth/registro", json={"nombre": "Test", "correo": "ia@t.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "ia@t.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", json={"nombre_negocio": "Barbería", "horario": "Lun-Sáb 10:00-19:00"}, headers=h).json()["id_negocio"]
    c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
           json={"nombre_servicio": "Degradado", "precio": 6700, "duracion_estimada": 30})
    return h, idn


def enviar(idn, texto_ia, mensaje="quiero reservar", tel="56911111111"):
    """Procesa un mensaje simulando que la IA respondió texto_ia."""
    ai_service.generar_respuesta = lambda prompt, provider=None, model=None: texto_ia
    ai_service.ahora_chile = lambda: AHORA
    db = SessionLocal()
    try:
        negocio = db.get(Negocio, idn)
        return chat_service.procesar_mensaje(db, negocio, tel, mensaje)
    finally:
        db.close()


def test_marcador():
    _, sol = agendado_ia.extraer_solicitud("[[AGENDAR|Degradado|2026-11-12|16:00]]")
    assert sol.servicio == "Degradado" and str(sol.fecha) == "2026-11-12" and sol.hora.hour == 16
    assert agendado_ia.extraer_solicitud("hola")[1] is None


def test_flujo():
    h, idn = preparar()
    marcador = "[[AGENDAR|degradado|2026-11-12|16:00]]"

    # Interruptor apagado: no agenda y el marcador no llega al cliente
    r = enviar(idn, marcador)
    assert "[[" not in r
    assert c.get(f"/negocio/{idn}/agenda", headers=h).json() == []

    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})

    r = enviar(idn, marcador)
    assert "Reservé" in r and "jueves 12-11" in r and "16:00" in r, r
    citas = c.get(f"/negocio/{idn}/agenda", headers=h).json()
    assert len(citas) == 1 and citas[0]["estado_cita"] == "pendiente"

    # El mismo cliente repite: no se duplica
    assert "Ya tienes reservado" in enviar(idn, marcador)
    # Otro cliente pide la misma hora: ocupada y sugiere alternativas
    r = enviar(idn, marcador, tel="56922222222")
    assert "ya están tomadas" in r and "16:30" in r, r
    # Fuera de horario
    assert "fuera de nuestro horario" in enviar(idn, "[[AGENDAR|Degradado|2026-11-12|20:00]]", tel="56933333333")
    # Hora pasada
    assert "ya pasó" in enviar(idn, "[[AGENDAR|Degradado|2026-11-09|12:00]]", tel="56933333333")
    # Servicio inexistente
    assert "No encontré" in enviar(idn, "[[AGENDAR|Tinte|2026-11-12|12:00]]", tel="56933333333")

    assert len(c.get(f"/negocio/{idn}/agenda", headers=h).json()) == 1


if __name__ == "__main__":
    test_marcador(); test_flujo(); print("OK agendado automático")
