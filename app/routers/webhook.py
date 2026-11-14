"""
RF-06 | Recepción de webhooks de WhatsApp.
RF-07 | Prompt dinámico (mensaje + contexto del negocio).
RF-08 | Procesamiento NLP.
RF-09 | Envío de la respuesta de vuelta a WhatsApp.

Este es el corazón del sistema: Meta dispara un evento -> lo procesamos ->
consultamos la IA -> respondemos. RNF-01 exige que todo el ciclo tome
menos de 15 segundos.
"""
from fastapi import APIRouter, Request, Query, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.config import WHATSAPP_VERIFY_TOKEN
from app.core.security import descifrar_texto
from app.models.negocio import Negocio
from app.services import whatsapp_service, chat_service

router = APIRouter(prefix="/webhook", tags=["Webhook WhatsApp"])


@router.get("")
def verificar_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta llama a este endpoint una sola vez para verificar el webhook
    (se configura en developers.facebook.com > WhatsApp > Configuration).
    Meta envía los parámetros con puntos (hub.mode, no hub_mode), por eso
    el alias es obligatorio."""
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge)
    return {"error": "Token de verificación inválido"}


@router.post("")
async def recibir_mensaje(request: Request, db: Session = Depends(get_db)):
    """Punto de entrada de cada mensaje entrante de un cliente por WhatsApp."""
    payload = await request.json()

    # 1) Extraer el mensaje y a qué número de TrepAI llegó (RF-06)
    telefono_cliente, texto_mensaje, phone_number_id = whatsapp_service.parsear_evento(payload)
    if not texto_mensaje or not phone_number_id:
        # Eventos que no son mensajes de texto (confirmaciones de lectura,
        # estados de entrega, etc.) — no hay nada que responder.
        return {"status": "ignorado"}

    # 2) Identificar a qué negocio pertenece este número (RNF-02: multitenant)
    negocio = db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == phone_number_id).first()
    if negocio is None:
        return {"status": "negocio_no_encontrado"}

    # 3) RF-04: si el administrador pausó el bot, no respondemos
    if not negocio.estado_bot:
        return {"status": "bot_inactivo"}

    # 4-7) Guardar mensaje, prompt dinámico, IA e historial (RF-05/07/08)
    respuesta = chat_service.procesar_mensaje(db, negocio, telefono_cliente, texto_mensaje)

    # 8) Responder al cliente por WhatsApp (RF-09)
    raw_token = (negocio.whatsapp_token or "").strip()
    token_whatsapp = raw_token if raw_token.startswith("EAA") else descifrar_texto(raw_token)
    if not token_whatsapp:
        return {"status": "procesado", "enviado_a_whatsapp": False, "motivo": "whatsapp_no_conectado"}

    enviado = whatsapp_service.enviar_mensaje(
        telefono_destino=telefono_cliente,
        texto=respuesta,
        phone_number_id=negocio.whatsapp_phone_number_id,
        token=token_whatsapp,
    )

    return {"status": "procesado", "enviado_a_whatsapp": enviado}
