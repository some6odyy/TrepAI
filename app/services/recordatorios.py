"""
Recordatorios y confirmaciones de citas.

Para cada cita activa (pendiente o confirmada) el sistema envía:

1. Aviso de cita agendada  -> apenas se crea (o se reagenda).
2. Recordatorio 24 h antes -> solo si la cita se agendó con más de 24 h de
   anticipación (si no, el aviso de agendado ya cumple ese rol).
3. Confirmación 1 h antes  -> le pide al cliente responder SÍ o NO, y deja un
   aviso para el negocio en el Dashboard (y por correo al administrador).
   - SÍ  -> la cita pasa a "confirmada".
   - NO  -> la cita pasa a "cancelada" y el horario queda libre.
   - Sin respuesta -> la cita se mantiene; el negocio ya vio el aviso.

Canales (a los datos de contacto que se pidieron al agendar):
- WhatsApp: texto libre si el cliente nos escribió en las últimas 24 h
  (regla de Meta); si no, una plantilla aprobada si está configurada
  (WHATSAPP_PLANTILLA_*). Sin plantilla, fuera de la ventana no se envía.
- Correo: siempre que haya SMTP configurado. El correo de confirmación trae
  enlaces "Confirmo" / "No podré ir", así funciona aunque WhatsApp no llegue.

¿Quién lo ejecuta? `ejecutar_pendientes()`:
- un cron externo cada 5 min llama a /tareas/recordatorios (ver README);
- además se ejecuta "de pasada" cuando llega un mensaje por WhatsApp y
  cuando el Dashboard consulta novedades, como respaldo.
Cada envío se "reclama" con un UPDATE atómico antes de mandarlo, así dos
ejecuciones simultáneas nunca duplican un mensaje.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import time as time_mod
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core import config
from app.core.tiempo import ahora_chile_naive, ahora_utc
from app.models.administrador import Administrador
from app.models.agenda import Agenda
from app.models.aviso import Aviso
from app.models.cliente import Cliente
from app.models.conversacion import Conversacion, Mensaje
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import agenda_service, email_service, notificacion_service

logger = logging.getLogger("trepai.recordatorios")

ESTADOS_ACTIVOS = ("pendiente", "confirmada")
HORAS_RECORDATORIO = 24
MINUTOS_CONFIRMACION = 60
VENTANA_META_HORAS = 24
# Hasta cuándo se acepta un SÍ/NO después de la hora de inicio
MINUTOS_GRACIA_RESPUESTA = 30
# Ejecución "de pasada" (webhook / Dashboard): como mucho una vez por minuto por negocio
SEGUNDOS_ENTRE_EJECUCIONES = 60

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_ultima_ejecucion: dict[int | None, float] = {}


# ----------------------------------------------------------------- utilidades

def _fecha_legible(fecha) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha:%d-%m}"


def _offset_local() -> timedelta:
    """Diferencia hora Chile - UTC (redondeada al minuto)."""
    diff = ahora_chile_naive() - ahora_utc()
    return timedelta(minutes=round(diff.total_seconds() / 60))


def _reclamar(db: Session, id_agenda: int, columna: str) -> bool:
    """Marca la notificación como enviada SOLO si nadie lo hizo antes.
    Devuelve True si esta ejecución ganó el derecho a enviarla."""
    col = getattr(Agenda, columna)
    filas = (
        db.query(Agenda)
        .filter(Agenda.id_agenda == id_agenda, col.is_(None))
        .update({col: ahora_utc()}, synchronize_session=False)
    )
    db.commit()
    return filas == 1


def crear_aviso(db: Session, id_negocio: int, tipo: str, texto: str, id_agenda: int | None = None) -> Aviso:
    aviso = Aviso(id_negocio=id_negocio, tipo=tipo, texto=texto, id_agenda=id_agenda)
    db.add(aviso)
    db.commit()
    db.refresh(aviso)
    return aviso


def ventana_abierta(db: Session, negocio: Negocio, telefono: str) -> bool:
    """True si ese número le escribió al negocio en las últimas 24 h (Meta
    solo permite mensajes de texto libre dentro de esa ventana)."""
    desde = ahora_utc() - timedelta(hours=VENTANA_META_HORAS)
    return (
        db.query(Mensaje.id_mensaje)
        .join(Conversacion, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .join(Cliente, Conversacion.id_cliente == Cliente.id_cliente)
        .filter(
            Conversacion.id_negocio == negocio.id_negocio,
            Cliente.telefono == telefono,
            Mensaje.emisor == "cliente",
            Mensaje.fecha_hora >= desde,
        )
        .first()
        is not None
    )


def registrar_en_historial(db: Session, negocio: Negocio, telefono: str, texto: str, emisor: str = "bot") -> None:
    """Deja el mensaje saliente en la conversación del cliente, para que el
    administrador vea en Conversaciones qué se le envió."""
    from app.services.chat_service import obtener_o_crear_conversacion_abierta  # evita import circular

    cliente = db.query(Cliente).filter(Cliente.telefono == telefono).first()
    if cliente is None:
        return
    conversacion = obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor=emisor, contenido=texto))
    db.commit()


def enviar_whatsapp(
    db: Session,
    negocio: Negocio,
    telefono: str | None,
    texto: str,
    plantilla: str = "",
    parametros: list[str] | None = None,
) -> str:
    """Devuelve "texto", "plantilla" o "" (no enviado)."""
    if not telefono:
        return ""
    if ventana_abierta(db, negocio, telefono):
        if notificacion_service.enviar_whatsapp_cloud(negocio, telefono, texto):
            registrar_en_historial(db, negocio, telefono, texto)
            return "texto"
        return ""
    if plantilla and parametros:
        if notificacion_service.enviar_plantilla_cloud(
            negocio, telefono, plantilla, config.WHATSAPP_PLANTILLA_IDIOMA, parametros
        ):
            registrar_en_historial(db, negocio, telefono, texto)
            return "plantilla"
        return ""
    logger.info(
        "Sin ventana de 24 h ni plantilla para %s (negocio %s): WhatsApp no enviado, solo correo",
        telefono, negocio.id_negocio,
    )
    return ""


# ------------------------------------------- enlaces de confirmar/cancelar

def generar_token_respuesta(id_agenda: int, valido_hasta: datetime) -> str:
    payload = {"a": id_agenda, "exp": int(valido_hasta.timestamp())}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    firma = hmac.new(config.SECRET_KEY.encode(), f"cita:{raw}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{firma}"


def verificar_token_respuesta(token: str) -> int | None:
    try:
        raw, firma = token.split(".", 1)
        esperada = hmac.new(config.SECRET_KEY.encode(), f"cita:{raw}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(firma, esperada):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if int(payload["exp"]) < int(time_mod.time()):
            return None
        return int(payload["a"])
    except Exception:  # noqa: BLE001
        return None


def _enlaces_respuesta(cita: Agenda) -> tuple[str, str]:
    inicio_utc = agenda_service.inicio_cita(cita) - _offset_local()
    # El enlace vale hasta 30 min después de la hora de la cita
    vence = (inicio_utc + timedelta(minutes=MINUTOS_GRACIA_RESPUESTA)).replace(tzinfo=timezone.utc)
    token = generar_token_respuesta(cita.id_agenda, vence)
    base = f"{config.APP_URL}/cita/respuesta?t={token}"
    return f"{base}&r=si", f"{base}&r=no"


# --------------------------------------------------------------- mensajes

def _datos(cita: Agenda, cliente: Cliente) -> tuple[str, str, str]:
    """(nombre, telefono, correo) a los que se notifica."""
    nombre = (cita.nombre_contacto or cliente.nombre_cliente or "").strip()
    telefono = cita.telefono_contacto or cliente.telefono
    correo = cita.correo_contacto or cliente.correo
    return nombre, telefono, correo


def _enviar_aviso_reserva(db, negocio, cita, cliente, servicio) -> None:
    nombre, telefono, correo = _datos(cita, cliente)
    cuando = f"{_fecha_legible(cita.fecha_cita)} a las {cita.hora_cita:%H:%M}"
    direccion = f"\n• Dirección: {negocio.direccion}" if negocio.direccion else ""
    texto = (
        f"📅 Hola{' *' + nombre + '*' if nombre else ''}, tu hora en *{negocio.nombre_negocio}* quedó agendada:\n"
        f"• Servicio: *{servicio.nombre_servicio}*\n"
        f"• Cuándo: *{cuando}*{direccion}\n"
        f"Te recordaremos antes y 1 hora antes te pediremos confirmar."
    )
    # Si reservó por este mismo chat, el bot ya se lo dijo: no repetimos por WhatsApp.
    reservo_por_chat = telefono == cliente.telefono and ventana_abierta(db, negocio, telefono)
    if not reservo_por_chat:
        enviar_whatsapp(
            db, negocio, telefono, texto,
            config.WHATSAPP_PLANTILLA_RECORDATORIO,
            [nombre or "cliente", servicio.nombre_servicio, negocio.nombre_negocio, cuando],
        )
    email_service.enviar_correo(
        correo,
        f"Tu hora en {negocio.nombre_negocio}: {cuando}",
        f"Hola {nombre or ''},\n\nTu hora quedó agendada.\n\n"
        f"Servicio: {servicio.nombre_servicio}\nCuándo: {cuando}\n"
        f"{'Dirección: ' + negocio.direccion + chr(10) if negocio.direccion else ''}"
        f"\nTe enviaremos un recordatorio y, 1 hora antes, te pediremos confirmar tu asistencia.\n\n"
        f"{negocio.nombre_negocio}",
        negocio.nombre_negocio,
    )
    crear_aviso(
        db, negocio.id_negocio, "nueva_cita",
        f"Nueva cita: {servicio.nombre_servicio} con {nombre or telefono} el {cuando}",
        cita.id_agenda,
    )


def _enviar_recordatorio_24h(db, negocio, cita, cliente, servicio) -> None:
    nombre, telefono, correo = _datos(cita, cliente)
    cuando = f"{_fecha_legible(cita.fecha_cita)} a las {cita.hora_cita:%H:%M}"
    texto = (
        f"⏰ Hola{' *' + nombre + '*' if nombre else ''}, te recordamos tu hora de "
        f"*{servicio.nombre_servicio}* en *{negocio.nombre_negocio}* el *{cuando}*.\n"
        f"Si no puedes asistir, respóndenos para reagendar."
    )
    enviar_whatsapp(
        db, negocio, telefono, texto,
        config.WHATSAPP_PLANTILLA_RECORDATORIO,
        [nombre or "cliente", servicio.nombre_servicio, negocio.nombre_negocio, cuando],
    )
    email_service.enviar_correo(
        correo,
        f"Recordatorio: {servicio.nombre_servicio} el {cuando}",
        f"Hola {nombre or ''},\n\nTe recordamos tu hora de {servicio.nombre_servicio} en "
        f"{negocio.nombre_negocio} el {cuando}.\n\nSi no puedes asistir, avísanos por WhatsApp.\n\n"
        f"{negocio.nombre_negocio}",
        negocio.nombre_negocio,
    )


def _enviar_confirmacion_1h(db, negocio, cita, cliente, servicio) -> None:
    nombre, telefono, correo = _datos(cita, cliente)
    hora = f"{cita.hora_cita:%H:%M}"
    texto = (
        f"Hola{' *' + nombre + '*' if nombre else ''} 👋 tu hora de *{servicio.nombre_servicio}* en "
        f"*{negocio.nombre_negocio}* es hoy a las *{hora}*.\n"
        f"¿Confirmas tu asistencia? Responde *SÍ* para confirmar o *NO* para cancelar."
    )
    enviar_whatsapp(
        db, negocio, telefono, texto,
        config.WHATSAPP_PLANTILLA_CONFIRMACION,
        [nombre or "cliente", servicio.nombre_servicio, negocio.nombre_negocio, hora],
    )
    link_si, link_no = _enlaces_respuesta(cita)
    email_service.enviar_correo(
        correo,
        f"¿Confirmas tu hora de hoy a las {hora}?",
        f"Hola {nombre or ''},\n\nTu hora de {servicio.nombre_servicio} en {negocio.nombre_negocio} "
        f"es hoy a las {hora}.\n\n"
        f"✔ Confirmo que voy:   {link_si}\n"
        f"✘ No podré ir (cancelar): {link_no}\n\n"
        f"También puedes responder SÍ o NO por WhatsApp.\n\n{negocio.nombre_negocio}",
        negocio.nombre_negocio,
    )

    # Aviso al negocio: en el Dashboard (en vivo) y por correo al administrador
    texto_aviso = (
        f"En 1 hora ({hora}): {servicio.nombre_servicio} con {nombre or telefono}. "
        f"Se le pidió confirmar."
    )
    crear_aviso(db, negocio.id_negocio, "cita_1h", texto_aviso, cita.id_agenda)
    admin = db.query(Administrador).filter(Administrador.id_administrador == negocio.id_administrador).first()
    if admin is not None:
        email_service.enviar_correo(
            admin.correo,
            f"En 1 hora: {servicio.nombre_servicio} con {nombre or telefono}",
            f"{texto_aviso}\n\nCliente: {nombre or '-'}\nTeléfono: {telefono}\nCorreo: {correo or '-'}\n"
            f"Estado actual: {cita.estado_cita}\n\nTe avisaremos en el Dashboard si responde.",
        )


def notificar_confirmacion_del_local(db, negocio, cita, cliente, servicio) -> None:
    """El local confirmó la cita desde la Agenda: se le avisa al cliente."""
    nombre, telefono, correo = _datos(cita, cliente)
    cuando = f"{_fecha_legible(cita.fecha_cita)} a las {cita.hora_cita:%H:%M}"
    texto = (
        f"✅ Hola{' *' + nombre + '*' if nombre else ''}, *{negocio.nombre_negocio}* confirmó tu hora:\n"
        f"• Servicio: *{servicio.nombre_servicio}*\n• Cuándo: *{cuando}*\n¡Te esperamos!"
    )
    enviar_whatsapp(
        db, negocio, telefono, texto,
        config.WHATSAPP_PLANTILLA_RECORDATORIO,
        [nombre or "cliente", servicio.nombre_servicio, negocio.nombre_negocio, cuando],
    )
    email_service.enviar_correo(
        correo,
        f"Hora confirmada: {servicio.nombre_servicio} el {cuando}",
        f"Hola {nombre or ''},\n\n{negocio.nombre_negocio} confirmó tu hora de "
        f"{servicio.nombre_servicio} el {cuando}.\n\n¡Te esperamos!",
        negocio.nombre_negocio,
    )


def notificar_cancelacion_del_local(db, negocio, cita, cliente, servicio, motivo: str | None = None) -> None:
    """El local canceló la cita desde la Agenda: se le avisa al cliente por
    WhatsApp (si hay ventana de 24 h o plantilla) y por correo."""
    nombre, telefono, correo = _datos(cita, cliente)
    cuando = f"{_fecha_legible(cita.fecha_cita)} a las {cita.hora_cita:%H:%M}"
    detalle = f"\nMotivo: {motivo.strip()}" if motivo and motivo.strip() else ""
    texto = (
        f"❌ Hola{' *' + nombre + '*' if nombre else ''}, *{negocio.nombre_negocio}* tuvo que cancelar tu hora:\n"
        f"• Servicio: *{servicio.nombre_servicio}*\n• Cuándo: *{cuando}*{detalle}\n"
        f"Disculpa las molestias 🙏 Si quieres otra hora, respóndenos por aquí con el día y la hora."
    )
    enviar_whatsapp(
        db, negocio, telefono, texto,
        config.WHATSAPP_PLANTILLA_RECORDATORIO,
        [nombre or "cliente", servicio.nombre_servicio, negocio.nombre_negocio, f"{cuando} (CANCELADA)"],
    )
    email_service.enviar_correo(
        correo,
        f"Hora cancelada: {servicio.nombre_servicio} el {cuando}",
        f"Hola {nombre or ''},\n\n{negocio.nombre_negocio} tuvo que cancelar tu hora de "
        f"{servicio.nombre_servicio} del {cuando}.{detalle}\n\n"
        f"Disculpa las molestias. Si quieres agendar otra hora, escríbenos por WhatsApp.\n\n"
        f"{negocio.nombre_negocio}",
        negocio.nombre_negocio,
    )


# --------------------------------------------------------------- motor

def procesar_cita(db: Session, negocio: Negocio, id_agenda: int, ahora_local: datetime | None = None) -> list[str]:
    """Envía lo que corresponda a UNA cita según la hora actual. Devuelve
    la lista de notificaciones enviadas (para logs y pruebas)."""
    ahora_local = (ahora_local or ahora_chile_naive()).replace(tzinfo=None)
    fila = (
        db.query(Agenda, Cliente, Servicio)
        .join(Cliente, Agenda.id_cliente == Cliente.id_cliente)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(Agenda.id_agenda == id_agenda, Servicio.id_negocio == negocio.id_negocio)
        .first()
    )
    if fila is None:
        return []
    cita, cliente, servicio = fila
    if cita.estado_cita not in ESTADOS_ACTIVOS:
        return []

    inicio = agenda_service.inicio_cita(cita)
    if inicio <= ahora_local:
        return []
    restante = inicio - ahora_local
    enviados: list[str] = []

    if cita.aviso_reserva_enviado is None and _reclamar(db, cita.id_agenda, "aviso_reserva_enviado"):
        _enviar_aviso_reserva(db, negocio, cita, cliente, servicio)
        enviados.append("reserva")

    if cita.recordatorio_24h_enviado is None and restante <= timedelta(hours=HORAS_RECORDATORIO):
        # ¿Se agendó con menos de 24 h? Entonces el aviso de agendado ya sirvió de recordatorio.
        inicio_utc = inicio - _offset_local()
        agendada_con_poca_anticipacion = (inicio_utc - cita.fecha_creacion) < timedelta(hours=HORAS_RECORDATORIO)
        muy_cerca = restante <= timedelta(minutes=MINUTOS_CONFIRMACION)
        if _reclamar(db, cita.id_agenda, "recordatorio_24h_enviado"):
            if not (agendada_con_poca_anticipacion or muy_cerca):
                _enviar_recordatorio_24h(db, negocio, cita, cliente, servicio)
                enviados.append("recordatorio_24h")

    if cita.confirmacion_1h_enviada is None and restante <= timedelta(minutes=MINUTOS_CONFIRMACION):
        if _reclamar(db, cita.id_agenda, "confirmacion_1h_enviada"):
            _enviar_confirmacion_1h(db, negocio, cita, cliente, servicio)
            enviados.append("confirmacion_1h")

    return enviados


def ejecutar_pendientes(db: Session, id_negocio: int | None = None, ahora_local: datetime | None = None) -> dict:
    """Recorre las citas activas futuras con notificaciones pendientes."""
    ahora_local = (ahora_local or ahora_chile_naive()).replace(tzinfo=None)
    negocios_q = db.query(Negocio)
    if id_negocio is not None:
        negocios_q = negocios_q.filter(Negocio.id_negocio == id_negocio)

    resumen = {"citas_revisadas": 0, "enviados": 0}
    for negocio in negocios_q.all():
        agenda_service.liberar_pendientes_vencidas(db, negocio.id_negocio)
        citas = (
            db.query(Agenda.id_agenda)
            .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
            .filter(
                Servicio.id_negocio == negocio.id_negocio,
                Agenda.estado_cita.in_(ESTADOS_ACTIVOS),
                Agenda.fecha_cita >= ahora_local.date(),
                (Agenda.aviso_reserva_enviado.is_(None))
                | (Agenda.recordatorio_24h_enviado.is_(None))
                | (Agenda.confirmacion_1h_enviada.is_(None)),
            )
            .all()
        )
        for (id_agenda,) in citas:
            resumen["citas_revisadas"] += 1
            try:
                resumen["enviados"] += len(procesar_cita(db, negocio, id_agenda, ahora_local))
            except Exception:  # noqa: BLE001 — una cita con problemas no frena a las demás
                db.rollback()
                logger.exception("Error procesando recordatorios de la cita %s", id_agenda)
    return resumen


def ejecutar_si_toca(db: Session, id_negocio: int) -> None:
    """Versión "de pasada" con límite de frecuencia (webhook / Dashboard)."""
    ahora = time_mod.monotonic()
    if ahora - _ultima_ejecucion.get(id_negocio, -1e9) < SEGUNDOS_ENTRE_EJECUCIONES:
        return
    _ultima_ejecucion[id_negocio] = ahora
    try:
        ejecutar_pendientes(db, id_negocio)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Error ejecutando recordatorios de pasada (negocio %s)", id_negocio)


# ----------------------------------------------------- respuesta SÍ / NO

_SI = {"si", "s", "confirmo", "confirmado", "confirmada", "ok", "okay", "dale", "ya", "listo", "yes", "voy", "obvio", "claro"}
_NO = {"no", "n", "cancelo", "cancelar", "cancela", "cancelado"}


def interpretar_respuesta(texto: str) -> str | None:
    """'Sí, voy' -> "si"; 'no puedo' -> "no"; cualquier otra cosa -> None."""
    limpio = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode().lower()
    if "👍" in (texto or "") and len(limpio.strip()) == 0:
        return "si"
    palabras = re.findall(r"[a-z]+", limpio)
    if not palabras or len(palabras) > 6:
        return None
    if palabras[0] in _NO or "no puedo" in limpio or "no voy" in limpio:
        return "no"
    if palabras[0] in _SI:
        return "si"
    return None


def cita_esperando_respuesta(
    db: Session, negocio: Negocio, telefono: str, ahora_local: datetime | None = None
) -> Agenda | None:
    """Cita de este número a la que ya se le pidió confirmar y no respondió."""
    ahora_local = (ahora_local or ahora_chile_naive()).replace(tzinfo=None)
    candidatas = (
        db.query(Agenda)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .join(Cliente, Agenda.id_cliente == Cliente.id_cliente)
        .filter(
            Servicio.id_negocio == negocio.id_negocio,
            Agenda.estado_cita.in_(ESTADOS_ACTIVOS),
            Agenda.confirmacion_1h_enviada.isnot(None),
            Agenda.respuesta_cliente.is_(None),
            (Cliente.telefono == telefono) | (Agenda.telefono_contacto == telefono),
        )
        .order_by(Agenda.fecha_cita, Agenda.hora_cita)
        .all()
    )
    limite = timedelta(minutes=MINUTOS_GRACIA_RESPUESTA)
    for cita in candidatas:
        if agenda_service.inicio_cita(cita) + limite >= ahora_local:
            return cita
    return None


def registrar_respuesta(db: Session, negocio: Negocio, cita: Agenda, respuesta: str) -> str:
    """Aplica SÍ/NO a la cita, avisa al negocio y devuelve el texto para el cliente."""
    servicio = db.query(Servicio).filter(Servicio.id_servicio == cita.id_servicio).first()
    cliente = db.query(Cliente).filter(Cliente.id_cliente == cita.id_cliente).first()
    nombre = cita.nombre_contacto or (cliente.nombre_cliente if cliente else None) or "El cliente"
    hora = f"{cita.hora_cita:%H:%M}"
    nombre_serv = servicio.nombre_servicio if servicio else "su servicio"

    cita.respuesta_cliente = respuesta
    if respuesta == "si":
        cita.estado_cita = "confirmada"
        db.commit()
        crear_aviso(db, negocio.id_negocio, "cliente_confirmo", f"✅ {nombre} confirmó su hora de las {hora} ({nombre_serv}).", cita.id_agenda)
        return f"¡Perfecto! ✅ Quedó confirmada tu hora de *{nombre_serv}* hoy a las *{hora}*. ¡Te esperamos!"

    cita.estado_cita = "cancelada"
    db.commit()
    crear_aviso(db, negocio.id_negocio, "cliente_cancelo", f"❌ {nombre} canceló su hora de las {hora} ({nombre_serv}). El horario quedó libre.", cita.id_agenda)
    return f"Listo, cancelamos tu hora de *{nombre_serv}* de las *{hora}*. Si quieres agendar otro día, escríbenos 🙌"
