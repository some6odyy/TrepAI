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
from app.models.contexto_ia import ContextoIA
from app.models.servicio import Servicio
from app.models.conversacion import Conversacion, Mensaje
from app.services import whatsapp_service, ai_service
from app.services.cliente_service import obtener_o_crear_cliente

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


def _obtener_o_crear_conversacion_abierta(db: Session, id_cliente: int, id_negocio: int) -> Conversacion:
    """Reutiliza la conversación abierta más reciente con este cliente, o
    abre una nueva si no hay ninguna (ej. primer contacto, o la anterior
    ya se cerró)."""
    conversacion = (
        db.query(Conversacion)
        .filter(
            Conversacion.id_cliente == id_cliente,
            Conversacion.id_negocio == id_negocio,
            Conversacion.estado == "abierta",
        )
        .order_by(Conversacion.fecha_inicio.desc())
        .first()
    )
    if conversacion is None:
        conversacion = Conversacion(id_cliente=id_cliente, id_negocio=id_negocio, estado="abierta")
        db.add(conversacion)
        db.commit()
        db.refresh(conversacion)
    return conversacion


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

    # 4) Cliente + conversación
    cliente = obtener_o_crear_cliente(db, telefono_cliente)
    conversacion = _obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)

    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="cliente", contenido=texto_mensaje))
    db.commit()

    # 5) Armar el prompt con el contexto configurado en RF-03 (RF-07)
    contexto = db.query(ContextoIA).filter(ContextoIA.id_negocio == negocio.id_negocio).first()
    catalogo = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()

    prompt = ai_service.armar_prompt(
        reglas_negocio=contexto.reglas_negocio if contexto else "",
        instrucciones=contexto.instrucciones if contexto else "",
        catalogo=[
            {
                "nombre_servicio": s.nombre_servicio,
                "precio": s.precio,
                "duracion_estimada": s.duracion_estimada,
            }
            for s in catalogo
        ],
        mensaje_cliente=texto_mensaje,
        catalogo_habilitado=contexto.consultar_catalogo if contexto else True,
    )

    # 6) Consultar el modelo de IA (RF-08) — placeholder hasta el paso 7
    respuesta = ai_service.generar_respuesta(
        prompt,
        provider=contexto.ai_provider if contexto else None,
        model=contexto.ai_model if contexto else None,
    )

    # 7) Guardar la respuesta del bot en el historial (RF-05)
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="bot", contenido=respuesta))
    db.commit()

    # 8) Responder al cliente por WhatsApp (RF-09)
    token_whatsapp = descifrar_texto(negocio.whatsapp_token)
    if not token_whatsapp:
        # El negocio tiene el bot activo pero nunca conectó (o le falló el
        # cifrado de) su token de WhatsApp — no hay a quién mandarle nada.
        return {"status": "procesado", "enviado_a_whatsapp": False, "motivo": "whatsapp_no_conectado"}

    enviado = whatsapp_service.enviar_mensaje(
        telefono_destino=telefono_cliente,
        texto=respuesta,
        phone_number_id=negocio.whatsapp_phone_number_id,
        token=token_whatsapp,
    )

    return {"status": "procesado", "enviado_a_whatsapp": enviado}
