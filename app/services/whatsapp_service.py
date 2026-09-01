"""
Capa de integración con WhatsApp Business Cloud API.
Aísla los detalles de la API de Meta del resto del sistema (RF-06, RF-09).

Cada negocio tiene su propio phone_number_id y token (multitenant), así
que las funciones los reciben como parámetro en vez de leerlos de un solo
.env global.
"""
import logging

import requests

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
