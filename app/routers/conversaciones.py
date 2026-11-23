"""RF-05 | Historial auditable: el administrador revisa qué habló el bot
con cada cliente. Fundamental para detectar respuestas erróneas de la IA.
Además, el administrador puede responder a mano desde el Dashboard."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.deps import obtener_negocio_propio
from app.models.negocio import Negocio
from app.models.conversacion import Conversacion, Mensaje
from app.models.cliente import Cliente
from app.models.agenda import Agenda
from app.models.servicio import Servicio
from app.core.tiempo import ahora_utc
from app.schemas.conversacion import ConversacionResponse, MensajeResponse, PausaBotRequest, ResponderRequest
from app.services import chat_service, recordatorios, whatsapp_service

router = APIRouter(prefix="/negocio/{id_negocio}/conversaciones", tags=["Historial"])


@router.get("", response_model=list[ConversacionResponse])
def listar_conversaciones(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Lista todas las conversaciones del negocio, con actividad más reciente
    primero, con la cantidad de mensajes de cada una."""
    resultados = (
        db.query(Conversacion, Cliente, func.count(Mensaje.id_mensaje), func.max(Mensaje.fecha_hora))
        .join(Cliente, Conversacion.id_cliente == Cliente.id_cliente)
        .outerjoin(Mensaje, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .filter(Conversacion.id_negocio == negocio.id_negocio)
        .group_by(Conversacion.id_conversacion, Cliente.id_cliente)
        .all()
    )

    # Último mensaje DEL CLIENTE por conversación -> ¿sigue abierta la ventana de 24 h?
    ultimos_cliente = dict(
        db.query(Mensaje.id_conversacion, func.max(Mensaje.fecha_hora))
        .join(Conversacion, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .filter(Conversacion.id_negocio == negocio.id_negocio, Mensaje.emisor == "cliente")
        .group_by(Mensaje.id_conversacion)
        .all()
    )
    # Respaldo para clientes sin nombre guardado (conversaciones antiguas):
    # el nombre que dieron en su cita más reciente en este negocio.
    nombres_cita = {}
    for id_cliente, nombre in (
        db.query(Agenda.id_cliente, Agenda.nombre_contacto)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(Servicio.id_negocio == negocio.id_negocio, Agenda.nombre_contacto.isnot(None))
        .order_by(Agenda.id_agenda)
        .all()
    ):
        nombres_cita[id_cliente] = nombre

    limite_ventana = ahora_utc() - timedelta(hours=recordatorios.VENTANA_META_HORAS)
    resultados.sort(key=lambda r: r[3] or r[0].fecha_inicio, reverse=True)

    return [
        ConversacionResponse(
            id_conversacion=conversacion.id_conversacion,
            id_cliente=cliente.id_cliente,
            nombre_cliente=cliente.nombre_cliente or nombres_cita.get(cliente.id_cliente),
            telefono_cliente=cliente.telefono,
            fecha_inicio=conversacion.fecha_inicio,
            fecha_fin=conversacion.fecha_fin,
            estado=conversacion.estado,
            total_mensajes=total_mensajes,
            ultima_actividad=ultima or conversacion.fecha_inicio,
            puede_responder=bool(
                ultimos_cliente.get(conversacion.id_conversacion)
                and ultimos_cliente[conversacion.id_conversacion] >= limite_ventana
            ),
            bot_pausado_hasta=(
                conversacion.bot_pausado_hasta
                if conversacion.bot_pausado_hasta and conversacion.bot_pausado_hasta > ahora_utc()
                else None
            ),
        )
        for conversacion, cliente, total_mensajes, ultima in resultados
    ]


@router.get("/{id_conversacion}/mensajes", response_model=list[MensajeResponse])
def listar_mensajes(
    id_conversacion: int,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Devuelve el detalle mensaje por mensaje de una conversación puntual."""
    conversacion = (
        db.query(Conversacion)
        .filter(
            Conversacion.id_conversacion == id_conversacion,
            Conversacion.id_negocio == negocio.id_negocio,
        )
        .first()
    )
    if conversacion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada")

    return (
        db.query(Mensaje)
        .filter(Mensaje.id_conversacion == id_conversacion)
        .order_by(Mensaje.fecha_hora)
        .all()
    )


@router.post("/{id_conversacion}/mensajes", response_model=MensajeResponse, status_code=status.HTTP_201_CREATED)
def responder_conversacion(
    id_conversacion: int,
    datos: ResponderRequest,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """El administrador responde a mano al cliente por WhatsApp. El mensaje
    queda en el historial con emisor "negocio"."""
    fila = (
        db.query(Conversacion, Cliente)
        .join(Cliente, Conversacion.id_cliente == Cliente.id_cliente)
        .filter(
            Conversacion.id_conversacion == id_conversacion,
            Conversacion.id_negocio == negocio.id_negocio,
        )
        .first()
    )
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada")
    conversacion, cliente = fila

    if not recordatorios.ventana_abierta(db, negocio, cliente.telefono):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Pasaron más de 24 horas desde el último mensaje del cliente. WhatsApp solo "
                "permite responder dentro de esa ventana; espera a que el cliente vuelva a escribir."
            ),
        )

    texto = datos.texto.strip()
    enviado = whatsapp_service.enviar_respuesta_negocio(negocio, cliente.telefono, texto)
    if enviado is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este negocio no tiene un número de WhatsApp conectado.",
        )
    if not enviado:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WhatsApp no aceptó el mensaje. Intenta de nuevo en unos segundos.",
        )

    mensaje = Mensaje(id_conversacion=conversacion.id_conversacion, emisor="negocio", contenido=texto)
    db.add(mensaje)
    db.commit()
    db.refresh(mensaje)
    # Tomar el control: el dueño está atendiendo esta conversación, así que
    # el bot se calla en ella por un rato (se puede reactivar desde el chat).
    chat_service.pausar_bot_en_conversacion(db, conversacion)
    return mensaje


@router.put("/{id_conversacion}/bot", response_model=ConversacionResponse)
def pausar_o_reactivar_bot(
    id_conversacion: int,
    datos: PausaBotRequest,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Pausa el bot solo en esta conversación (el dueño la atiende a mano)
    o lo reactiva."""
    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.id_conversacion == id_conversacion, Conversacion.id_negocio == negocio.id_negocio)
        .first()
    )
    if conversacion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada")
    if datos.pausado:
        chat_service.pausar_bot_en_conversacion(db, conversacion, datos.horas)
    else:
        chat_service.reactivar_bot_en_conversacion(db, conversacion)
    return next(c for c in listar_conversaciones(db, negocio) if c.id_conversacion == id_conversacion)
