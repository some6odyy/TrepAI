import logging
import os
import requests

from app.models.agenda import Agenda
from app.models.cliente import Cliente
from app.models.negocio import Negocio
from app.models.servicio import Servicio

logger = logging.getLogger(__name__)

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
GRAPH_API_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0")


def _fecha_legible(fecha) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha:%d-%m}"


def _obtener_credenciales_meta(negocio: Negocio) -> tuple[str, str]:
    """Obtiene phone_number_id y access_token desde el modelo Negocio o variables de entorno."""
    phone_id = (
        getattr(negocio, "whatsapp_phone_id", None)
        or getattr(negocio, "phone_number_id", None)
        or getattr(negocio, "id_telefono_whatsapp", None)
        or os.getenv("WHATSAPP_PHONE_ID", "")
    )
    token = (
        getattr(negocio, "whatsapp_token", None)
        or getattr(negocio, "access_token", None)
        or getattr(negocio, "token_whatsapp", None)
        or os.getenv("WHATSAPP_TOKEN", "")
    )
    return str(phone_id or "").strip(), str(token or "").strip()


def enviar_whatsapp_cloud(negocio: Negocio, telefono: str, texto: str) -> bool:
    phone_id, token = _obtener_credenciales_meta(negocio)
    if not phone_id or not token:
        logger.warning(
            "Negocio %s sin credenciales de WhatsApp Cloud API configuradas",
            getattr(negocio, "id_negocio", "?"),
        )
        return False

    telefono_limpio = "".join(ch for ch in (telefono or "") if ch.isdigit())
    if not telefono_limpio:
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono_limpio,
        "type": "text",
        "text": {"preview_url": False, "body": texto},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if not resp.ok:
            logger.warning(
                "Meta rechazó el envío a %s (%s): %s",
                telefono_limpio,
                resp.status_code,
                resp.text,
            )
            return False
        return True
    except Exception as exc:
        logger.warning("Error de red enviando WhatsApp a %s: %s", telefono_limpio, exc)
        return False


def notificar_cita_confirmada(
    negocio: Negocio,
    cita: Agenda,
    cliente: Cliente,
    servicio: Servicio,
) -> bool:
    """Notifica al cliente cuando su cita pasa a estado 'confirmada'."""
    saludo = f"Hola *{cliente.nombre_cliente}*, " if cliente.nombre_cliente else "¡Hola! "
    cuando = f"*{_fecha_legible(cita.fecha_cita)}* a las *{cita.hora_cita:%H:%M}*"
    nombre_local = getattr(negocio, "nombre_negocio", None) or getattr(negocio, "nombre", "")
    en_local = f" en *{nombre_local}*" if nombre_local else ""

    mensaje = (
        f"✅ {saludo}se confirmó tu cita{en_local}:\n"
        f"• Servicio: *{servicio.nombre_servicio}*\n"
        f"• Cuándo: {cuando}\n"
        f"¡Te esperamos!"
    )
    return enviar_whatsapp_cloud(negocio, cliente.telefono, mensaje)
