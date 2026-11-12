import logging
import os
import requests

from app.models.agenda import Agenda
from app.models.cliente import Cliente
from app.models.negocio import Negocio
from app.models.servicio import Servicio

logger = logging.getLogger(__name__)

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

def _fecha_legible(fecha) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha:%d-%m}"


def enviar_whatsapp_bridge(telefono: str, texto: str) -> bool:
    """Envía un mensaje saliente a través del bridge local (whatsapp-web.js)."""
    puente_token = os.getenv("PUENTE_TOKEN", "").strip()
    if not puente_token:
        return False

    bridge_url = os.getenv("PUENTE_URL", "http://127.0.0.1:3001").rstrip("/")
    try:
        resp = requests.post(
            f"{bridge_url}/enviar",
            headers={"x-puente-token": puente_token},
            json={"telefono": telefono, "texto": texto},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("No se pudo enviar WhatsApp a %s vía bridge: %s", telefono, exc)
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
    return enviar_whatsapp_bridge(cliente.telefono, mensaje)