"""Pruebas de la validación de horario de atención.
    PYTHONPATH=. python tests/test_horario.py
"""
import os, tempfile, uuid
from datetime import date, time

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services.horario_service import (  # noqa: E402
    FueraDeHorarioError, parsear_horario, validar_dentro_de_horario,
)

LUN, MAR, SAB, DOM = date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 12), date(2026, 12, 13)


def ok(h, f, t, d=30):
    validar_dentro_de_horario(h, f, t, d)


def mal(h, f, t, d=30):
    try:
        validar_dentro_de_horario(h, f, t, d)
    except FueraDeHorarioError:
        return
    raise AssertionError(f"debió rechazar {h!r} {f} {t}")


def test_parser_y_reglas():
    for txt in ["Lun-Sáb 10:00-19:00", "Lunes a sábado, 10:00 a 19:00", "LUN A SAB 10 A 19", "Lun-Sab 10hrs-19hrs"]:
        h = "Lun-Sáb 10:00-19:00" if txt == "Lun-Sáb 10:00-19:00" else txt
        assert parsear_horario(h) == {d: [(600, 1140)] for d in range(6)}, txt

    base = "Lun-Sáb 10:00-19:00"
    ok(base, LUN, time(10, 0)); ok(base, LUN, time(18, 30))     # límites exactos
    mal(base, LUN, time(9, 59)); mal(base, LUN, time(18, 31))   # termina pasadas las 19:00
    mal(base, LUN, time(18, 45), 30); ok(base, LUN, time(18, 45), 15)
    mal(base, DOM, time(12, 0))                                  # domingo cerrado

    partido = "Lun-Vie 10:00-14:00 y 15:00-19:00"
    ok(partido, LUN, time(13, 30)); mal(partido, LUN, time(14, 0)); mal(partido, LUN, time(13, 45))
    ok(partido, LUN, time(15, 0)); mal(partido, SAB, time(11, 0))

    variado = "Lun-Vie 10:00-19:00, Sáb 10:00-14:00"
    ok(variado, SAB, time(13, 30)); mal(variado, SAB, time(14, 0)); ok(variado, MAR, time(17, 0))

    salteado = "Lun, Mié y Vie 10:00-14:00"
    ok(salteado, LUN, time(10, 0)); mal(salteado, MAR, time(10, 0))

    assert parsear_horario("24 horas") == {d: [(0, 1440)] for d in range(7)}
    ok("Todos los días 09:00-21:00", DOM, time(20, 0))

    # Sin horario o ininterpretable: no se bloquea nada
    ok(None, DOM, time(3, 0)); ok("", DOM, time(3, 0)); ok("cuando se pueda", DOM, time(3, 0))
    assert parsear_horario("10:00-19:00 lun-vie") is None       # ambiguo
    assert parsear_horario("Lun-Vie 19:00-10:00") is None       # fin antes del inicio


def test_api():
    c = TestClient(app)
    c.post("/auth/registro", json={"nombre": "Test", "correo": "h@t.cl", "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": "h@t.cl", "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    # no deja guardar un horario que no se entiende
    assert c.post("/negocio", json={"nombre_negocio": "X", "horario": "cuando se pueda"}, headers=h).status_code == 422

    idn = c.post("/negocio", json={"nombre_negocio": "Silvabarber", "horario": "Lun-Sáb 10:00-19:00"},
                 headers=h).json()["id_negocio"]
    serv = c.post(f"/negocio/{idn}/contexto/servicios", headers=h,
                  json={"nombre_servicio": "Corte", "precio": 8000, "duracion_estimada": 30}).json()["id_servicio"]

    def cita(fecha, hora, tel="+56911111111"):
        return c.post(f"/negocio/{idn}/agenda", headers=h, json={
            "telefono_cliente": tel, "id_servicio": serv, "fecha_cita": fecha, "hora_cita": hora})

    assert cita("2026-12-07", "10:00:00").status_code == 201
    r = cita("2026-12-07", "09:30:00"); assert r.status_code == 422 and "horario" in r.json()["detail"].lower()
    assert cita("2026-12-07", "18:45:00").status_code == 422     # termina 19:15
    r = cita("2026-12-13", "12:00:00"); assert r.status_code == 422 and "domingo" in r.json()["detail"]
    assert cita("2026-12-07", "10:00:00", "+56922222222").status_code == 409  # sigue valiendo el bloqueo

    # cambiar el horario del negocio cambia lo permitido
    assert c.put(f"/negocio/{idn}/perfil", headers=h, json={"horario": "Lun-Dom 08:00-22:00"}).status_code == 200
    assert cita("2026-12-13", "12:00:00").status_code == 201
    assert c.put(f"/negocio/{idn}/perfil", headers=h, json={"horario": "???"}).status_code == 422

    # reactivar una cita cancelada fuera del nuevo horario se rechaza
    id_dom = c.get(f"/negocio/{idn}/agenda?fecha=2026-12-13", headers=h).json()[0]["id_agenda"]
    c.patch(f"/negocio/{idn}/agenda/{id_dom}/estado", headers=h, json={"estado_cita": "cancelada"})
    c.put(f"/negocio/{idn}/perfil", headers=h, json={"horario": "Lun-Sáb 10:00-19:00"})
    r = c.patch(f"/negocio/{idn}/agenda/{id_dom}/estado", headers=h, json={"estado_cita": "confirmada"})
    assert r.status_code == 422, r.text


if __name__ == "__main__":
    test_parser_y_reglas(); test_api()
    print("OK: todas las pruebas de horario pasaron")
