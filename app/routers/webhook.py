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
from app.models.negocio import Negocio
from app.services import whatsapp_service, chat_service, recordatorios, cliente_service

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

    # Nombre del perfil de WhatsApp: se guarda si todavía no sabemos cómo se
    # llama el cliente (el nombre que dé al agendar tiene prioridad).
    cliente_service.obtener_o_crear_cliente(db, telefono_cliente, whatsapp_service.nombre_perfil(payload))

    # 3) Respuesta SÍ/NO al mensaje de confirmación de 1 hora antes: se
    #    aplica a la cita sin pasar por la IA (funciona aunque el bot esté pausado).
    respuesta = chat_service.procesar_respuesta_confirmacion(db, negocio, telefono_cliente, texto_mensaje)

    if respuesta is None:
        # 4) RF-04: si el administrador pausó el bot, no respondemos con IA, pero
        #    el mensaje queda en Conversaciones para responderlo a mano.
        if not negocio.estado_bot:
            chat_service.guardar_mensaje_sin_respuesta(db, negocio, telefono_cliente, texto_mensaje)
            return {"status": "bot_inactivo"}

        # 5-7) Guardar mensaje, prompt dinámico, IA e historial (RF-05/07/08)
        respuesta = chat_service.procesar_mensaje(db, negocio, telefono_cliente, texto_mensaje)

    # 8) Responder al cliente por WhatsApp (RF-09)
    enviado = whatsapp_service.enviar_respuesta_negocio(negocio, telefono_cliente, respuesta)

    # 9) De pasada: avisos de citas recién agendadas y recordatorios vencidos
    recordatorios.ejecutar_si_toca(db, negocio.id_negocio)

    if enviado is None:
        return {"status": "procesado", "enviado_a_whatsapp": False, "motivo": "whatsapp_no_conectado"}
    return {"status": "procesado", "enviado_a_whatsapp": enviado}
