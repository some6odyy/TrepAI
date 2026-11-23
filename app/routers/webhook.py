"""
RF-06 | Recepción de webhooks de WhatsApp.
RF-07 | Prompt dinámico (mensaje + contexto del negocio).
RF-08 | Procesamiento NLP.
RF-09 | Envío de la respuesta de vuelta a WhatsApp.

Este es el corazón del sistema: Meta dispara un evento -> lo procesamos ->
consultamos la IA -> respondemos. RNF-01 exige que todo el ciclo tome
menos de 15 segundos.
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core import config
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
    inicio = time.monotonic()  # RNF-01: se mide hasta que la respuesta sale
    cuerpo = await request.body()

    # 0) Seguridad: el evento tiene que venir firmado por Meta con la clave
    #    secreta de la app. Sin META_APP_SECRET (desarrollo local) no se verifica.
    if config.META_APP_SECRET and not whatsapp_service.firma_valida(
        cuerpo, request.headers.get("X-Hub-Signature-256"), config.META_APP_SECRET
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Firma inválida")
    try:
        payload = json.loads(cuerpo or b"{}")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON inválido")

    # 1) Extraer el mensaje y a qué número de TrepAI llegó (RF-06)
    entrante = whatsapp_service.parsear_mensaje(payload)
    if entrante is None or entrante.tipo == "reaction":
        # Estados de entrega/lectura y reacciones: no hay nada que responder.
        return {"status": "ignorado"}

    # 2) Identificar a qué negocio pertenece este número (RNF-02: multitenant)
    negocio = db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == entrante.phone_number_id).first()
    if negocio is None:
        return {"status": "negocio_no_encontrado"}

    # 3) Si el webhook tardó, Meta reenvía el mismo mensaje: se descarta para
    #    no contestar ni agendar dos veces.
    if not chat_service.registrar_evento(db, entrante.wamid):
        return {"status": "duplicado"}

    try:
        return _procesar(db, negocio, payload, entrante, inicio)
    except Exception:
        # Que el reintento de Meta sí se procese
        chat_service.olvidar_evento(db, entrante.wamid)
        raise


def _procesar(db: Session, negocio: Negocio, payload: dict, entrante, inicio: float) -> dict:
    telefono_cliente = entrante.telefono

    # Nombre del perfil de WhatsApp: se guarda si todavía no sabemos cómo se
    # llama el cliente (el nombre que dé al agendar tiene prioridad).
    cliente_service.obtener_o_crear_cliente(db, telefono_cliente, whatsapp_service.nombre_perfil(payload))

    resultado = None
    if entrante.es_texto:
        # 4) Respuesta SÍ/NO al mensaje de confirmación de 1 hora antes: se
        #    aplica a la cita sin pasar por la IA (funciona aunque el bot esté pausado).
        resultado = chat_service.procesar_respuesta_confirmacion_detalle(
            db, negocio, telefono_cliente, entrante.texto, inicio
        )

    if resultado is None:
        contenido = entrante.texto if entrante.es_texto else entrante.descripcion
        tipo = "texto" if entrante.es_texto else entrante.tipo
        # 5) RF-04: si el administrador pausó el bot (en todo el negocio o solo
        #    en esta conversación porque la está atendiendo a mano), no se
        #    responde, pero el mensaje queda en Conversaciones.
        if not negocio.estado_bot:
            chat_service.guardar_mensaje_sin_respuesta(db, negocio, telefono_cliente, contenido, tipo)
            return {"status": "bot_inactivo"}
        if chat_service.conversacion_en_pausa(db, negocio, telefono_cliente):
            chat_service.guardar_mensaje_sin_respuesta(db, negocio, telefono_cliente, contenido, tipo)
            return {"status": "conversacion_en_pausa"}

        if entrante.es_texto:
            # 6-8) Guardar mensaje, prompt dinámico, IA e historial (RF-05/07/08)
            resultado = chat_service.procesar_mensaje_detalle(db, negocio, telefono_cliente, entrante.texto, inicio)
        else:
            # Audio, foto, sticker...: la IA solo lee texto
            resultado = chat_service.procesar_no_texto(
                db, negocio, telefono_cliente, entrante.descripcion, entrante.tipo, inicio
            )

    respuesta, mensaje_bot = resultado

    # 9) Responder al cliente por WhatsApp (RF-09) y registrar cuánto tardó (RNF-01)
    enviado = whatsapp_service.enviar_respuesta_negocio(negocio, telefono_cliente, respuesta)
    chat_service.completar_latencia(db, mensaje_bot, inicio)

    # 10) De pasada: avisos de citas recién agendadas y recordatorios vencidos
    recordatorios.ejecutar_si_toca(db, negocio.id_negocio)

    if enviado is None:
        return {"status": "procesado", "enviado_a_whatsapp": False, "motivo": "whatsapp_no_conectado"}
    return {"status": "procesado", "enviado_a_whatsapp": enviado}
