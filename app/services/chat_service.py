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
from app.models.aviso import Aviso
from app.services import ai_service, agendado_ia, agenda_service, recordatorios, reserva_sin_ia, respuesta_sin_ia
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


def procesar_respuesta_confirmacion(
    db: Session, negocio: Negocio, telefono_cliente: str, texto_mensaje: str
) -> str | None:
    """Si a este número se le pidió confirmar su cita (mensaje de 1 hora
    antes) y responde SÍ o NO, se aplica sin pasar por la IA. Funciona
    aunque el bot esté pausado: es parte del flujo de la agenda.
    Devuelve el texto de respuesta, o None si el mensaje no era eso."""
    cita = recordatorios.cita_esperando_respuesta(db, negocio, telefono_cliente)
    if cita is None:
        return None
    respuesta = recordatorios.interpretar_respuesta(texto_mensaje)
    if respuesta is None:
        return None

    cliente = obtener_o_crear_cliente(db, telefono_cliente)
    conversacion = obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="cliente", contenido=texto_mensaje))
    db.commit()

    texto_respuesta = recordatorios.registrar_respuesta(db, negocio, cita, respuesta)
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="bot", contenido=texto_respuesta))
    db.commit()
    return texto_respuesta


def guardar_mensaje_sin_respuesta(db: Session, negocio: Negocio, telefono_cliente: str, texto_mensaje: str) -> None:
    """Con el bot pausado, el mensaje igual queda en Conversaciones para que
    el negocio pueda responderlo a mano desde el Dashboard."""
    cliente = obtener_o_crear_cliente(db, telefono_cliente)
    conversacion = obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)
    db.add(Mensaje(id_conversacion=conversacion.id_conversacion, emisor="cliente", contenido=texto_mensaje))
    db.commit()


def _agendar_sin_ia(db, negocio, cliente, contexto, catalogo, historial, texto_mensaje) -> str | None:
    """Flujo de reserva sin IA (ver reserva_sin_ia.py). Devuelve None si el
    mensaje no tiene que ver con agendar."""
    if not catalogo:
        return None
    ahora = ai_service.ahora_chile()
    hoy = ahora.date()
    if not reserva_sin_ia.mensaje_aporta(texto_mensaje, catalogo, hoy):
        return None
    datos = reserva_sin_ia.reunir_datos(
        reserva_sin_ia.mensajes_desde_ultima_reserva(historial, texto_mensaje), catalogo, hoy
    )
    if not (datos.fecha or datos.hora or (datos.servicio and (datos.correo or datos.telefono or datos.nombre))):
        return None  # solo nombró un servicio: lo responde respuesta_sin_ia (precio y duración)
    if datos.faltan:
        return reserva_sin_ia.mensaje_faltantes(datos)
    if not (contexto and contexto.agendar_automatico):
        return (
            f"Anoté *{datos.servicio.nombre_servicio}* para el {datos.fecha:%d-%m} a las {datos.hora:%H:%M} "
            f"a nombre de {datos.nombre}. Alguien del equipo revisa la disponibilidad y te confirma a la brevedad. 🙌"
        )
    solicitud = agendado_ia.SolicitudCita(
        accion="AGENDAR",
        servicio=datos.servicio.nombre_servicio,
        fecha=datos.fecha,
        hora=datos.hora,
        nombre=datos.nombre,
        correo=datos.correo,
        telefono=datos.telefono,
    )
    return agendado_ia.procesar_solicitud(db, negocio, cliente.id_cliente, solicitud, ahora)


def _avisar_ia_no_disponible(db: Session, negocio: Negocio, cliente) -> None:
    """Un aviso por cliente mientras no se lea, para no llenar la campana."""
    quien = cliente.nombre_cliente or cliente.telefono
    texto = f"⚠️ La IA no estuvo disponible para responder a {quien}. Se le envió info básica; revisa Conversaciones."
    ya_existe = (
        db.query(Aviso.id_aviso)
        .filter(
            Aviso.id_negocio == negocio.id_negocio,
            Aviso.tipo == "ia_no_disponible",
            Aviso.leido.is_(False),
            Aviso.texto == texto,
        )
        .first()
    )
    if ya_existe is None:
        recordatorios.crear_aviso(db, negocio.id_negocio, "ia_no_disponible", texto)


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

    catalogo_dicts = [
        {
            "nombre_servicio": s.nombre_servicio,
            "precio": s.precio,
            "duracion_estimada": s.duracion_estimada,
        }
        for s in catalogo
    ]
    perfil = {
        "nombre_negocio": negocio.nombre_negocio,
        "direccion": negocio.direccion,
        "telefono": negocio.telefono,
        "horario": negocio.horario,
    }

    prompt = ai_service.armar_prompt(
        reglas_negocio=contexto.reglas_negocio if contexto else "",
        instrucciones=contexto.instrucciones if contexto else "",
        catalogo=catalogo_dicts,
        mensaje_cliente=texto_mensaje,
        catalogo_habilitado=contexto.consultar_catalogo if contexto else True,
        historial=historial,
        agendar_automatico=bool(contexto and contexto.agendar_automatico),
        cita_activa=cita_activa_txt,
        datos_cliente={
            "nombre": cliente.nombre_cliente,
            "correo": cliente.correo,
            "whatsapp": cliente.telefono,
        },
        perfil_negocio=perfil,
    )

    # RF-08: consultar el modelo de IA
    # (prueba el modelo elegido y, si falla, modelos de respaldo)
    respuesta = ai_service.generar_respuesta_o_none(
        prompt,
        provider=contexto.ai_provider if contexto else None,
        model=contexto.ai_model if contexto else None,
    )

    if respuesta is None:
        # Ningún modelo respondió. 1) Si el cliente está dando datos para
        # agendar, se juntan entre mensajes y se reserva con las mismas reglas.
        respuesta = _agendar_sin_ia(db, negocio, cliente, contexto, catalogo, historial, texto_mensaje)

    if respuesta is None:
        # 2) Si no, contestamos con los datos del negocio (servicios, horario,
        # dirección) y avisamos al dueño para que siga él.
        respuesta = respuesta_sin_ia.generar(
            texto_mensaje,
            perfil,
            catalogo_dicts,
            catalogo_habilitado=contexto.consultar_catalogo if contexto else True,
            nombre_cliente=cliente.nombre_cliente,
            ya_saludo=any(emisor != "cliente" for emisor, _ in historial),
            catalogo_ya_enviado=any(
                emisor != "cliente" and "nuestros servicios" in contenido for emisor, contenido in historial
            ),
        )
        _avisar_ia_no_disponible(db, negocio, cliente)

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
