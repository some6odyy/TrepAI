"""
Capa de integración con WhatsApp Business Cloud API.
Aísla los detalles de la API de Meta del resto del sistema (RF-06, RF-09).

Cada negocio tiene su propio phone_number_id y token (multitenant), así
que las funciones los reciben como parámetro en vez de leerlos de un solo
.env global.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass

import requests

from app.core.security import descifrar_texto

logger = logging.getLogger("trepai.whatsapp")

GRAPH_API_VERSION = "v19.0"


def parsear_evento(payload: dict) -> tuple[str | None, str | None, str | None]:
    """Extrae (telefono_cliente, texto_mensaje, phone_number_id_receptor)
    del payload crudo del webhook.

    El phone_number_id_receptor es el número DE TREPAI que recibió el
    mensaje — con eso identificamos a qué negocio pertenece (RNF-02).

    Devuelve (None, None, None) si el evento no trae un mensaje de texto
    (ej. es solo una confirmación de lectura o un mensaje de otro tipo)."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        mensaje = value["messages"][0]
        telefono = mensaje["from"]
        texto = mensaje.get("text", {}).get("body")
        return telefono, texto, phone_number_id
    except (KeyError, IndexError):
        return None, None, None


# Mensajes que no son texto: cómo se muestran en Conversaciones
_DESCRIPCION_TIPO = {
    "audio": "🎤 Audio",
    "voice": "🎤 Audio",
    "image": "📷 Imagen",
    "video": "🎬 Video",
    "sticker": "💬 Sticker",
    "document": "📄 Documento",
    "location": "📍 Ubicación",
    "contacts": "👤 Contacto",
}


@dataclass
class MensajeEntrante:
    telefono: str
    phone_number_id: str
    tipo: str  # text | audio | image | ... (tipo de Meta)
    wamid: str | None = None
    texto: str | None = None  # None si no es texto
    descripcion: str | None = None  # para los que no son texto ("🎤 Audio")

    @property
    def es_texto(self) -> bool:
        return bool(self.texto)


def parsear_mensaje(payload: dict) -> MensajeEntrante | None:
    """Como parsear_evento, pero también reconoce los mensajes que no son
    texto (audio, foto, sticker...) y trae el ID del mensaje (wamid) para
    descartar reenvíos de Meta. None si el evento no trae un mensaje (ej.
    estados de entrega o lectura)."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        phone_number_id = value["metadata"]["phone_number_id"]
        mensaje = value["messages"][0]
        telefono = mensaje["from"]
    except (KeyError, IndexError, TypeError):
        return None
    tipo = mensaje.get("type") or ("text" if "text" in mensaje else "desconocido")
    entrante = MensajeEntrante(telefono=telefono, phone_number_id=phone_number_id, tipo=tipo, wamid=mensaje.get("id"))
    if tipo == "text":
        entrante.texto = (mensaje.get("text") or {}).get("body")
    elif tipo == "button":  # respuesta rápida de una plantilla
        entrante.texto = (mensaje.get("button") or {}).get("text")
    elif tipo == "interactive":
        interactivo = mensaje.get("interactive") or {}
        entrante.texto = (interactivo.get("button_reply") or interactivo.get("list_reply") or {}).get("title")
    if not entrante.texto:
        descripcion = _DESCRIPCION_TIPO.get(tipo, "Mensaje no compatible")
        leyenda = ((mensaje.get(tipo) or {}).get("caption") or "") if isinstance(mensaje.get(tipo), dict) else ""
        entrante.descripcion = f"[{descripcion}]" + (f" {leyenda}" if leyenda else "")
    return entrante


def firma_valida(cuerpo: bytes, firma_header: str | None, app_secret: str) -> bool:
    """Verifica la cabecera X-Hub-Signature-256 ("sha256=<hex>") que Meta
    calcula con la clave secreta de la app sobre el cuerpo crudo del evento."""
    if not firma_header or not firma_header.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma_header.split("=", 1)[1], esperada)


def nombre_perfil(payload: dict) -> str | None:
    """Nombre de perfil de WhatsApp del cliente (value.contacts[0].profile.name).
    Meta lo manda en cada mensaje entrante; puede faltar o venir vacío."""
    try:
        nombre = payload["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]
    except (KeyError, IndexError, TypeError):
        return None
    nombre = " ".join(str(nombre or "").split())
    return nombre[:100] or None


def enviar_mensaje(telefono_destino: str, texto: str, phone_number_id: str, token: str) -> bool:
    """Despacha la respuesta de vuelta al cliente (RF-09).

    Devuelve True/False en vez de lanzar una excepción: si Meta o la red
    fallan, no queremos tumbar el webhook completo — el mensaje ya quedó
    guardado en la BD y se puede reintentar o revisar manualmente."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": telefono_destino,
        "type": "text",
        "text": {"body": texto},
    }

    try:
        respuesta = requests.post(url, headers=headers, json=body, timeout=10)
        respuesta.raise_for_status()
        return True
    except requests.RequestException as error:
        logger.error("Fallo al enviar mensaje de WhatsApp a %s: %s", telefono_destino, error)
        return False


def token_del_negocio(negocio) -> str | None:
    raw_token = (negocio.whatsapp_token or "").strip()
    if not raw_token:
        return None
    return raw_token if raw_token.startswith("EAA") else descifrar_texto(raw_token)


def enviar_respuesta_negocio(negocio, telefono_destino: str, texto: str) -> bool | None:
    """Envía texto con el número del negocio. None si el negocio no tiene
    WhatsApp conectado; True/False según si Meta lo aceptó."""
    token = token_del_negocio(negocio)
    if not token or not negocio.whatsapp_phone_number_id:
        return None
    return enviar_mensaje(
        telefono_destino=telefono_destino,
        texto=texto,
        phone_number_id=negocio.whatsapp_phone_number_id,
        token=token,
    )
