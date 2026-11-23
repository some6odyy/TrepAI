"""Mejoras finales: mensajes duplicados de Meta, firma del webhook, mensajes
que no son texto, límite de intentos, tomar el control de una conversación,
bloqueos de horario, editar citas, vista Clientes, Mi cuenta y tiempo de
respuesta (RNF-01).
    PYTHONPATH=. python -m pytest tests/test_mejoras_finales.py
"""
import hashlib, hmac, json, os, tempfile, uuid
from datetime import date, datetime, timedelta

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core import config  # noqa: E402
from app.core.tiempo import ahora_chile_naive  # noqa: E402
from app.services import ai_service, email_service, notificacion_service, whatsapp_service  # noqa: E402

c = TestClient(app)


def _negocio(sufijo: str, horario="Lun-Dom 09:00-21:00"):
    correo = f"mejoras{sufijo}@test.cl"
    c.post("/auth/registro", json={"nombre": "Dueño", "correo": correo, "contrasena": "Clave1234!"})
    tok = c.post("/auth/login", json={"correo": correo, "contrasena": "Clave1234!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    idn = c.post("/negocio", headers=h, json={"nombre_negocio": "Barbería Sin Pelos", "horario": horario}).json()["id_negocio"]
    servs = [
        c.post(f"/negocio/{idn}/contexto/servicios", headers=h, json={"nombre_servicio": n, "precio": p, "duracion_estimada": d}).json()["id_servicio"]
        for n, p, d in [("Corte clásico", 10000, 30), ("Corte y barba", 16000, 60)]
    ]
    c.put(f"/negocio/{idn}/contexto", headers=h, json={"agendar_automatico": True})
    pnid = f"PNID-MEJ-{sufijo}"
    c.put(f"/negocio/{idn}/whatsapp", headers=h, json={"phone_number_id": pnid, "access_token": "EAAtestToken123456"})
    c.patch(f"/negocio/{idn}/estado-bot?activo=true", headers=h)
    return h, idn, servs, pnid


def _evento(pnid, tel, mensaje: dict, nombre="Cliente"):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": pnid},
        "contacts": [{"profile": {"name": nombre}}],
        "messages": [{"from": tel, **mensaje}],
    }}]}]}


def _texto(pnid, tel, texto, wamid=None, nombre="Cliente"):
    m = {"type": "text", "text": {"body": texto}}
    if wamid:
        m["id"] = wamid
    return _evento(pnid, tel, m, nombre)


def _sin_envios():
    enviados = []
    whatsapp_service.enviar_mensaje = lambda **k: enviados.append(k["texto"]) or True
    notificacion_service.enviar_whatsapp_cloud = lambda *a: True
    email_service.enviar_correo = lambda *a, **k: True
    return enviados


def _proximo(dia_semana: int, hora: str) -> tuple[str, str]:
    hoy = ahora_chile_naive().date()
    d = hoy + timedelta(days=((dia_semana - hoy.weekday()) % 7) or 7)
    return d.isoformat(), hora


def test_webhook_duplicados_firma_y_no_texto():
    enviados = _sin_envios()
    ai_service.generar_respuesta_o_none = lambda *a, **k: "¡Hola! ¿En qué te ayudo?"
    h, idn, _, pnid = _negocio("w")

    # Meta reenvía el mismo mensaje (mismo wamid): se responde una sola vez
    r1 = c.post("/webhook", json=_texto(pnid, "56911110000", "hola", wamid="wamid.AAA"))
    r2 = c.post("/webhook", json=_texto(pnid, "56911110000", "hola", wamid="wamid.AAA"))
    assert r1.json()["status"] == "procesado" and r2.json()["status"] == "duplicado"
    assert len(enviados) == 1

    # Audio: queda en Conversaciones y se le pide que escriba
    enviados.clear()
    r = c.post("/webhook", json=_evento(pnid, "56911110000", {"id": "wamid.AUD", "type": "audio", "audio": {"id": "x"}}))
    assert r.json()["status"] == "procesado" and "solo puedo leer mensajes de texto" in enviados[-1]
    conv = c.get(f"/negocio/{idn}/conversaciones", headers=h).json()[0]
    mensajes = c.get(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/mensajes", headers=h).json()
    assert any(m["contenido"] == "[🎤 Audio]" and m["tipo_mensaje"] == "audio" for m in mensajes)
    # Foto con texto: se guarda la leyenda
    c.post("/webhook", json=_evento(pnid, "56911110000", {"id": "wamid.IMG", "type": "image", "image": {"caption": "este corte"}}))
    mensajes = c.get(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/mensajes", headers=h).json()
    assert any(m["contenido"] == "[📷 Imagen] este corte" for m in mensajes)
    # Las reacciones y los estados de entrega se ignoran
    assert c.post("/webhook", json=_evento(pnid, "56911110000", {"id": "wamid.R", "type": "reaction", "reaction": {"emoji": "👍"}})).json()["status"] == "ignorado"

    # Firma de Meta (X-Hub-Signature-256) con META_APP_SECRET configurado
    config.META_APP_SECRET = "secreto-de-prueba"
    cuerpo = json.dumps(_texto(pnid, "56911110000", "firmado", wamid="wamid.F1")).encode()
    firma = "sha256=" + hmac.new(b"secreto-de-prueba", cuerpo, hashlib.sha256).hexdigest()
    assert c.post("/webhook", content=cuerpo, headers={"Content-Type": "application/json"}).status_code == 403
    assert c.post("/webhook", content=cuerpo, headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=malo"}).status_code == 403
    r = c.post("/webhook", content=cuerpo, headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma})
    assert r.status_code == 200 and r.json()["status"] == "procesado"


def test_tomar_el_control_y_latencia():
    enviados = _sin_envios()
    ai_service.generar_respuesta_o_none = lambda *a, **k: "Respuesta del bot"
    h, idn, _, pnid = _negocio("p")
    tel = "56922220000"
    c.post("/webhook", json=_texto(pnid, tel, "hola", nombre="Camila"))
    conv = c.get(f"/negocio/{idn}/conversaciones", headers=h).json()[0]
    assert conv["bot_pausado_hasta"] is None

    # El dueño responde a mano -> el bot se calla en esa conversación
    r = c.post(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/mensajes", headers=h, json={"texto": "Hola Camila, te atiendo yo"})
    assert r.status_code == 201
    conv = c.get(f"/negocio/{idn}/conversaciones", headers=h).json()[0]
    assert conv["bot_pausado_hasta"] is not None
    enviados.clear()
    r = c.post("/webhook", json=_texto(pnid, tel, "perfecto, gracias"))
    assert r.json()["status"] == "conversacion_en_pausa" and enviados == []
    avisos = c.get(f"/negocio/{idn}/novedades", headers=h).json()["avisos"]
    assert any(a["tipo"] == "mensaje_en_pausa" for a in avisos)
    # Otro cliente sigue con el bot
    assert c.post("/webhook", json=_texto(pnid, "56922221111", "hola")).json()["status"] == "procesado"

    # Reactivar el bot en esa conversación
    r = c.put(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/bot", headers=h, json={"pausado": False})
    assert r.status_code == 200 and r.json()["bot_pausado_hasta"] is None
    assert c.post("/webhook", json=_texto(pnid, tel, "otra consulta")).json()["status"] == "procesado"
    # Pausar a mano por 1 hora
    r = c.put(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/bot", headers=h, json={"pausado": True, "horas": 1})
    assert r.json()["bot_pausado_hasta"] is not None

    # RNF-01: cada respuesta del bot queda con su latencia
    t = c.get(f"/negocio/{idn}/tiempo-respuesta", headers=h).json()
    assert t["total"] >= 3 and t["dentro_del_limite_pct"] == 100.0 and t["promedio_s"] < 15, t
    mensajes = c.get(f"/negocio/{idn}/conversaciones/{conv['id_conversacion']}/mensajes", headers=h).json()
    assert all(m["latencia_ms"] is not None for m in mensajes if m["emisor"] == "bot")


def test_limite_de_intentos():
    c.post("/auth/registro", json={"nombre": "Dueño", "correo": "intentos@test.cl", "contrasena": "Clave1234!"})
    for _ in range(5):
        assert c.post("/auth/login", json={"correo": "intentos@test.cl", "contrasena": "mala"}).status_code == 401
    r = c.post("/auth/login", json={"correo": "intentos@test.cl", "contrasena": "Clave1234!"})
    assert r.status_code == 429 and "Demasiados intentos" in r.json()["detail"], r.text
    # Otra cuenta desde la misma IP sigue pudiendo entrar
    c.post("/auth/registro", json={"nombre": "Otra", "correo": "otra@test.cl", "contrasena": "Clave1234!"})
    assert c.post("/auth/login", json={"correo": "otra@test.cl", "contrasena": "Clave1234!"}).status_code == 200

    # Recuperación: máximo 3 correos por hora a la misma cuenta (respuesta igual)
    correos = []
    import app.routers.auth as rauth
    rauth.enviar_correo_recuperacion = lambda *a, **k: correos.append(a[0])
    for _ in range(5):
        r = c.post("/auth/forgot-password", json={"correo": "otra@test.cl"})
        assert r.status_code == 200
    assert len(correos) == 3
    # Y por IP, como máximo 10 solicitudes por hora
    codigos = [c.post("/auth/forgot-password", json={"correo": f"x{i}@test.cl"}).status_code for i in range(8)]
    assert codigos[-1] == 429, codigos


def test_bloqueos_y_editar_cita():
    enviados = _sin_envios()
    ai_service.generar_respuesta_o_none = lambda *a, **k: None  # sin IA
    h, idn, (corte, barba), pnid = _negocio("b")
    lunes, _ = _proximo(0, "")

    # Almuerzo todos los lunes a viernes 14:00-15:00
    r = c.post(f"/negocio/{idn}/bloqueos", headers=h, json={"tipo": "semanal", "dias_semana": [0, 1, 2, 3, 4], "hora_desde": "14:00", "hora_hasta": "15:00", "motivo": "Almuerzo"})
    assert r.status_code == 201 and r.json()["descripcion"] == "De lunes a viernes, 14:00-15:00", r.text
    # Vacaciones: el martes siguiente completo
    martes = (date.fromisoformat(lunes) + timedelta(days=1)).isoformat()
    r = c.post(f"/negocio/{idn}/bloqueos", headers=h, json={"tipo": "fechas", "fecha_desde": martes, "motivo": "Vacaciones"})
    id_vacaciones = r.json()["id_bloqueo"]
    assert r.status_code == 201
    # Validaciones del formulario
    assert c.post(f"/negocio/{idn}/bloqueos", headers=h, json={"tipo": "semanal", "dias_semana": [], "hora_desde": "14:00", "hora_hasta": "15:00"}).status_code == 422
    assert c.post(f"/negocio/{idn}/bloqueos", headers=h, json={"tipo": "fechas", "fecha_desde": martes, "hora_desde": "15:00", "hora_hasta": "14:00"}).status_code == 422
    assert len(c.get(f"/negocio/{idn}/bloqueos", headers=h).json()) == 2

    base = {"telefono_cliente": "+56933330000", "nombre_cliente": "Ana Soto", "correo_cliente": "ana@correo.cl", "id_servicio": corte}
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={**base, "fecha_cita": lunes, "hora_cita": "14:15:00"})
    assert r.status_code == 409 and "Almuerzo" in r.json()["detail"], r.text
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={**base, "fecha_cita": martes, "hora_cita": "11:00:00"})
    assert r.status_code == 409 and "no atiende" in r.json()["detail"] and "Vacaciones" in r.json()["detail"], r.text
    # 13:30 + 30 min termina justo a las 14:00: se puede
    r = c.post(f"/negocio/{idn}/agenda", headers=h, json={**base, "fecha_cita": lunes, "hora_cita": "13:30:00"})
    assert r.status_code == 201, r.text
    cita = r.json()

    # El bot sin IA también respeta los bloqueos
    def decir(texto, tel="56944440000"):
        enviados.clear()
        c.post("/webhook", json=_texto(pnid, tel, texto, nombre="Pedro"))
        return enviados[-1]
    r = decir(f"Corte clásico el {martes[8:10]}-{martes[5:7]} a las 12, Pedro, pedro@correo.cl, 944440000")
    assert "no atendemos" in r and "Vacaciones" in r, r
    r = decir("el lunes a las 14:30")
    assert "no atendemos" in r and "Almuerzo" in r and "15:00" in r, r

    # Editar la cita: cambiar a "Corte y barba" (60 min) desde las 13:30 choca con el almuerzo
    r = c.put(f"/negocio/{idn}/agenda/{cita['id_agenda']}", headers=h, json={"id_servicio": barba})
    assert r.status_code == 409 and "Almuerzo" in r.json()["detail"], r.text
    # Moverla a las 16:00 con el servicio nuevo y otro correo
    r = c.put(f"/negocio/{idn}/agenda/{cita['id_agenda']}", headers=h, json={"id_servicio": barba, "hora_cita": "16:00", "correo_cliente": "ana.soto@correo.cl"})
    assert r.status_code == 200, r.text
    editada = r.json()
    assert editada["nombre_servicio"] == "Corte y barba" and editada["hora_cita"].startswith("16:00")
    assert editada["correo_cliente"] == "ana.soto@correo.cl"
    assert c.put(f"/negocio/{idn}/agenda/{cita['id_agenda']}", headers=h, json={"telefono_contacto": "123"}).status_code == 422
    # Hora que ya pasó
    ayer = (ahora_chile_naive().date() - timedelta(days=1)).isoformat()
    assert c.put(f"/negocio/{idn}/agenda/{cita['id_agenda']}", headers=h, json={"fecha_cita": ayer}).status_code == 422
    # Quitar el bloqueo de vacaciones libera el día
    assert c.delete(f"/negocio/{idn}/bloqueos/{id_vacaciones}", headers=h).status_code == 204
    r = c.put(f"/negocio/{idn}/agenda/{cita['id_agenda']}", headers=h, json={"fecha_cita": martes, "hora_cita": "11:00"})
    assert r.status_code == 200, r.text


def test_clientes_y_mi_cuenta():
    _sin_envios()
    ai_service.generar_respuesta_o_none = lambda *a, **k: "Hola"
    h, idn, (corte, _), pnid = _negocio("c")
    lunes, _ = _proximo(0, "")
    base = {"telefono_cliente": "+56955550000", "nombre_cliente": "Diego Muñoz", "correo_cliente": "diego@correo.cl", "id_servicio": corte}
    cita = c.post(f"/negocio/{idn}/agenda", headers=h, json={**base, "fecha_cita": lunes, "hora_cita": "11:00:00"}).json()
    c.patch(f"/negocio/{idn}/agenda/{cita['id_agenda']}/estado", headers=h, json={"estado_cita": "no_asistio"})
    c.post(f"/negocio/{idn}/agenda", headers=h, json={**base, "fecha_cita": lunes, "hora_cita": "12:00:00"})
    c.post("/webhook", json=_texto(pnid, "56966660000", "hola", nombre="Valentina"))

    clientes = c.get(f"/negocio/{idn}/clientes", headers=h).json()
    assert {x["nombre"] for x in clientes} == {"Diego Muñoz", "Valentina"}
    diego = next(x for x in clientes if x["nombre"] == "Diego Muñoz")
    assert diego["total_citas"] == 2 and diego["inasistencias"] == 1 and diego["proxima_cita"]["hora_cita"].startswith("12:00")
    assert [x["nombre"] for x in c.get(f"/negocio/{idn}/clientes?buscar=5555", headers=h).json()] == ["Diego Muñoz"]
    r = c.put(f"/negocio/{idn}/clientes/{diego['id_cliente']}/notas", headers=h, json={"notas": "Prefiere las tardes"})
    assert r.json()["notas"] == "Prefiere las tardes" and len(r.json()["citas"]) == 2
    # Otro negocio no ve a este cliente
    h2, idn2, _, _ = _negocio("c2")
    assert c.get(f"/negocio/{idn2}/clientes/{diego['id_cliente']}", headers=h2).status_code == 404
    assert c.get(f"/negocio/{idn}/clientes", headers=h2).status_code == 403

    # Mi cuenta
    assert c.put("/auth/yo", headers=h, json={"nombre": "Yohel Faundez"}).json()["nombre"] == "Yohel Faundez"
    assert c.put("/auth/yo", headers=h, json={"correo": "nuevo@test.cl"}).status_code == 400  # falta la contraseña
    r = c.put("/auth/yo", headers=h, json={"correo": "nuevo@test.cl", "contrasena_actual": "Clave1234!"})
    assert r.status_code == 200 and r.json()["correo"] == "nuevo@test.cl"
    assert c.post("/auth/cambiar-contrasena", headers=h, json={"contrasena_actual": "mala", "nueva_contrasena": "Nueva12345"}).status_code == 400
    assert c.post("/auth/cambiar-contrasena", headers=h, json={"contrasena_actual": "Clave1234!", "nueva_contrasena": "Nueva12345"}).status_code == 200
    assert c.post("/auth/login", json={"correo": "nuevo@test.cl", "contrasena": "Nueva12345"}).status_code == 200
