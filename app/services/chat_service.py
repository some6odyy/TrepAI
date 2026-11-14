"""
Lógica común para procesar un mensaje entrante de un cliente (RF-05, RF-07,
RF-08), sin importar por dónde llegó:

  - /webhook  -> WhatsApp Business Cloud API oficial (Meta)
  - /puente   -> puente de demostración con whatsapp-web.js (bridge/)

Así ambos canales usan exactamente el mismo prompt, la misma IA y quedan
guardados en el mismo historial que muestra el Dashboard.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.negocio import Negocio
from app.models.contexto_ia import ContextoIA
from app.models.servicio import Servicio
from app.models.conversacion import Conversacion, Mensaje
from app.services import ai_service, agendado_ia, agenda_service
from app.services.cliente_service import obtener_o_crear_cliente


# Cuántos mensajes previos ve la IA y de qué ventana de tiempo
MENSAJES_MEMORIA = 10
HORAS_MEMORIA = 12


def obtener_o_crear_conversacion_abierta(db: Session, id_cliente: int, id_negocio: int) -> Conversacion:
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


def procesar_mensaje(db: Session, negocio: Negocio, telefono_cliente: str, texto_mensaje: str) -> str:
    """Guarda el mensaje del cliente, arma el prompt con el contexto del
    negocio, consulta la IA, guarda la respuesta y la devuelve.

    No envía nada: cada canal (webhook o puente) se encarga de despachar
    la respuesta por su propio medio."""
    cliente = obtener_o_crear_cliente(db, telefono_cliente)
    conversacion = obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)

    # Memoria del bot: últimos mensajes de esta conversación (antes de guardar
    # el nuevo), solo de las últimas horas para no arrastrar temas de días atrás.
    desde = datetime.utcnow() - timedelta(hours=HORAS_MEMORIA)
    anteriores = (
        db.query(Mensaje)
        .filter(
            Mensaje.id_conversacion == conversacion.id_conversacion,
            Mensaje.fecha_hora >= desde,
        )
        .order_by(Mensaje.id_mensaje.desc())
        .limit(MENSAJES_MEMORIA)
        .all()
    )
    # Los mensajes de respaldo no aportan contexto a la IA, se omiten
    historial = [
        (m.emisor, m.contenido)
        for m in reversed(anteriores)
        if m.contenido != ai_service.RESPUESTA_DE_RESPALDO
    ]

    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="cliente", contenido=texto_mensaje))
    db.commit()

    # RF-07: prompt dinámico con el contexto configurado en RF-03
    contexto = db.query(ContextoIA).filter(ContextoIA.id_negocio == negocio.id_negocio).first()
    catalogo = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()

    cita_vigente = agenda_service.obtener_cita_activa_cliente(db, negocio.id_negocio, cliente.id_cliente)
    if cita_vigente is not None:
        cita_obj, serv_obj = cita_vigente
        cita_activa_txt = (
            f"{serv_obj.nombre_servicio} el {cita_obj.fecha_cita:%Y-%m-%d} "
            f"a las {cita_obj.hora_cita:%H:%M} (estado: {cita_obj.estado_cita})"
        )
    else:
        cita_activa_txt = None

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
        historial=historial,
        agendar_automatico=bool(contexto and contexto.agendar_automatico),
        cita_activa=cita_activa_txt,
        perfil_negocio={
            "nombre_negocio": negocio.nombre_negocio,
            "direccion": negocio.direccion,
            "telefono": negocio.telefono,
            "horario": negocio.horario,
        },
    )

    # RF-08: consultar el modelo de IA
    respuesta = ai_service.generar_respuesta(
        prompt,
        provider=contexto.ai_provider if contexto else None,
        model=contexto.ai_model if contexto else None,
    )

    # Agendado automático: si la IA pidió reservar ([[AGENDAR|...]]), el
    # backend crea la cita pendiente con las mismas reglas del Dashboard y
    # arma el mensaje según el resultado real.
    texto_ia, solicitud = agendado_ia.extraer_solicitud(respuesta)
    if solicitud is not None and contexto and contexto.agendar_automatico:
        respuesta = agendado_ia.procesar_solicitud(
            db, negocio, cliente.id_cliente, solicitud, ai_service.ahora_chile()
        )
    elif solicitud is not None:
        # El marcador no debe llegar nunca al cliente
        respuesta = texto_ia or "¿Para qué día y hora te gustaría agendar? Un miembro del equipo te confirmará."

    # RF-05: guardar la respuesta del bot en el historial
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="bot", contenido=respuesta))
    db.commit()

    return respuesta
