"""Pestaña "Casos de uso" del Dashboard: demostrar en vivo cada requerimiento.

- GET  /negocio/{id}/demo/estado   -> qué tan configurado/funcionando está cada RF/RNF
- POST /negocio/{id}/demo/mensaje  -> simulador de WhatsApp: el mensaje pasa por
  EXACTAMENTE el mismo flujo que uno real (webhook: bot on/off, pausa, confirmación
  SÍ/NO, prompt dinámico, IA o modo sin IA, agenda), pero la respuesta se muestra
  en el Dashboard en vez de enviarse por WhatsApp.
- POST /negocio/{id}/demo/prompt   -> el prompt que recibiría la IA (RF-07)
- POST /negocio/{id}/demo/reiniciar -> borra el rastro del cliente de prueba para repetir la demo

Los "clientes de prueba" usan números reservados (+56 9 0000 000X), así el
simulador nunca toca las citas ni conversaciones de clientes reales.
"""
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import MAX_RESPONSE_LATENCY_SECONDS
from app.core.deps import obtener_administrador_actual, obtener_negocio_propio
from app.core.tiempo import ahora_utc
from app.db.database import get_db
from app.models.administrador import Administrador
from app.models.agenda import Agenda
from app.models.cliente import Cliente
from app.models.contexto_ia import ContextoIA
from app.models.conversacion import Conversacion, Mensaje
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import agenda_service, chat_service, recordatorios
from app.services.cliente_service import obtener_o_crear_cliente

router = APIRouter(prefix="/negocio/{id_negocio}/demo", tags=["Casos de uso (demo)"])

# Clientes de prueba: +56 9 0000 0001 ... +56 9 0000 0009
PREFIJO_DEMO = "5690000000"


def _telefono_demo(telefono: str) -> str:
    digitos = agenda_service.normalizar_telefono(telefono)
    if not digitos or not digitos.startswith(PREFIJO_DEMO):
        raise HTTPException(status_code=400, detail="El simulador solo usa números de prueba (+56 9 0000 000X)")
    return digitos


class MensajeDemo(BaseModel):
    telefono: str = Field(default="56900000001")
    texto: str = Field(min_length=1, max_length=2000)
    nombre_perfil: str | None = Field(default=None, max_length=60)


class TelefonoDemo(BaseModel):
    telefono: str = Field(default="56900000001")


def _citas_json(db: Session, negocio: Negocio, cliente: Cliente) -> list[dict]:
    return [
        {
            "id_agenda": c.id_agenda,
            "servicio": s.nombre_servicio,
            "fecha_cita": c.fecha_cita.isoformat(),
            "hora_cita": c.hora_cita.strftime("%H:%M"),
            "duracion": agenda_service.duracion_minutos(s),
            "estado_cita": c.estado_cita,
            "nombre_contacto": c.nombre_contacto,
            "correo_contacto": c.correo_contacto,
            "telefono_contacto": c.telefono_contacto,
        }
        for c, s in agenda_service.citas_activas_cliente(db, negocio.id_negocio, cliente.id_cliente)
    ]


@router.post("/mensaje")
def simular_mensaje(
    datos: MensajeDemo,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Simulador de WhatsApp: mismos pasos que /webhook (ver webhook._procesar)."""
    inicio = time.monotonic()
    telefono = _telefono_demo(datos.telefono)
    cliente = obtener_o_crear_cliente(db, telefono, datos.nombre_perfil or "Cliente de prueba")

    estado, origen = "procesado", None
    resultado = chat_service.procesar_respuesta_confirmacion_detalle(db, negocio, telefono, datos.texto, inicio)
    if resultado is not None:
        origen = "confirmacion"
    elif not negocio.estado_bot:
        chat_service.guardar_mensaje_sin_respuesta(db, negocio, telefono, datos.texto)
        estado = "bot_inactivo"
    elif chat_service.conversacion_en_pausa(db, negocio, telefono):
        chat_service.guardar_mensaje_sin_respuesta(db, negocio, telefono, datos.texto)
        estado = "conversacion_en_pausa"
    else:
        resultado = chat_service.procesar_mensaje_detalle(db, negocio, telefono, datos.texto, inicio)

    respuesta, latencia_ms = None, int((time.monotonic() - inicio) * 1000)
    if resultado is not None:
        respuesta, mensaje = resultado
        chat_service.completar_latencia(db, mensaje, inicio)
        origen = origen or mensaje.origen_respuesta
        latencia_ms = mensaje.latencia_ms or latencia_ms
    recordatorios.ejecutar_si_toca(db, negocio.id_negocio)

    db.refresh(cliente)
    return {
        "estado": estado,
        "respuesta": respuesta,
        "origen": origen,  # ia | sin_ia | confirmacion
        "latencia_ms": latencia_ms,
        "limite_ms": MAX_RESPONSE_LATENCY_SECONDS * 1000,
        "citas_activas": _citas_json(db, negocio, cliente),
    }


@router.get("/citas")
def citas_cliente_demo(
    telefono: str = "56900000001",
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    cliente = db.query(Cliente).filter(Cliente.telefono == _telefono_demo(telefono)).first()
    return [] if cliente is None else _citas_json(db, negocio, cliente)


@router.get("/conversacion")
def conversacion_demo(
    telefono: str = "56900000001",
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Mensajes de la conversación abierta del cliente de prueba (para
    repintar el simulador al volver a la pestaña)."""
    cliente = db.query(Cliente).filter(Cliente.telefono == _telefono_demo(telefono)).first()
    if cliente is None:
        return []
    conv = (
        db.query(Conversacion)
        .filter(Conversacion.id_cliente == cliente.id_cliente, Conversacion.id_negocio == negocio.id_negocio,
                Conversacion.estado == "abierta")
        .order_by(Conversacion.fecha_inicio.desc())
        .first()
    )
    if conv is None:
        return []
    mensajes = (
        db.query(Mensaje).filter(Mensaje.id_conversacion == conv.id_conversacion)
        .order_by(Mensaje.id_mensaje.desc()).limit(60).all()
    )
    return [
        {"emisor": m.emisor, "contenido": m.contenido, "origen": m.origen_respuesta, "latencia_ms": m.latencia_ms}
        for m in reversed(mensajes)
    ]


@router.post("/prompt")
def ver_prompt(
    datos: MensajeDemo,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-07: el prompt dinámico que se le enviaría a la IA para este mensaje
    (no llama a la IA ni guarda nada)."""
    telefono = _telefono_demo(datos.telefono)
    cliente = obtener_o_crear_cliente(db, telefono, datos.nombre_perfil or "Cliente de prueba")
    conversacion = chat_service.obtener_o_crear_conversacion_abierta(db, cliente.id_cliente, negocio.id_negocio)
    historial = chat_service.historial_para_ia(db, conversacion.id_conversacion)
    prompt, contexto, *_ = chat_service.armar_prompt_cliente(db, negocio, cliente, datos.texto, historial)
    return {
        "prompt": prompt,
        "proveedor": contexto.ai_provider if contexto else None,
        "modelo": contexto.ai_model if contexto else None,
        "caracteres": len(prompt),
    }


@router.post("/reiniciar")
def reiniciar_cliente_demo(
    datos: TelefonoDemo,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Cancela las citas activas del cliente de prueba y cierra su
    conversación, para repetir la demostración desde cero."""
    cliente = db.query(Cliente).filter(Cliente.telefono == _telefono_demo(datos.telefono)).first()
    if cliente is None:
        return {"citas_canceladas": 0, "conversaciones_cerradas": 0}
    ids_servicios = db.query(Servicio.id_servicio).filter(Servicio.id_negocio == negocio.id_negocio)
    citas = (
        db.query(Agenda)
        .filter(Agenda.id_cliente == cliente.id_cliente, Agenda.id_servicio.in_(ids_servicios),
                Agenda.estado_cita.in_(("pendiente", "confirmada")))
        .all()
    )
    for cita in citas:
        cita.estado_cita = "cancelada"
    convs = (
        db.query(Conversacion)
        .filter(Conversacion.id_cliente == cliente.id_cliente, Conversacion.id_negocio == negocio.id_negocio,
                Conversacion.estado == "abierta")
        .all()
    )
    for conv in convs:
        conv.estado = "cerrada"
        conv.fecha_fin = ahora_utc()
        conv.bot_pausado_hasta = None
    db.commit()
    return {"citas_canceladas": len(citas), "conversaciones_cerradas": len(convs)}


@router.get("/estado")
def estado_casos_de_uso(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
    administrador: Administrador = Depends(obtener_administrador_actual),
):
    """Evidencia rápida de cada requerimiento, calculada con datos reales."""
    contexto = db.query(ContextoIA).filter(ContextoIA.id_negocio == negocio.id_negocio).first()
    n_servicios = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).count()
    convs = db.query(Conversacion.id_conversacion).filter(Conversacion.id_negocio == negocio.id_negocio)
    n_conversaciones = convs.count()
    mensajes = db.query(Mensaje).filter(Mensaje.id_conversacion.in_(convs))
    n_cliente = mensajes.filter(Mensaje.emisor == "cliente").count()
    n_bot = mensajes.filter(Mensaje.emisor == "bot").count()
    n_ia = mensajes.filter(Mensaje.emisor == "bot", Mensaje.origen_respuesta == "ia").count()
    n_sin_ia = mensajes.filter(Mensaje.emisor == "bot", Mensaje.origen_respuesta == "sin_ia").count()
    ultimo_cliente = mensajes.filter(Mensaje.emisor == "cliente").order_by(Mensaje.id_mensaje.desc()).first()

    latencias = [
        m.latencia_ms for m in mensajes.filter(
            Mensaje.emisor == "bot", Mensaje.latencia_ms.isnot(None),
            Mensaje.fecha_hora >= ahora_utc() - timedelta(days=7),
        ).all()
    ]
    limite_ms = MAX_RESPONSE_LATENCY_SECONDS * 1000

    ids_servicios = db.query(Servicio.id_servicio).filter(Servicio.id_negocio == negocio.id_negocio)
    citas = db.query(Agenda).filter(Agenda.id_servicio.in_(ids_servicios))
    activas = citas.filter(Agenda.estado_cita.in_(("pendiente", "confirmada"))).all()
    por_cliente: dict[int, int] = {}
    for c in activas:
        por_cliente[c.id_cliente] = por_cliente.get(c.id_cliente, 0) + 1

    otro = db.query(Negocio.id_negocio).filter(Negocio.id_negocio != negocio.id_negocio).first()

    return {
        "administrador": {"nombre": administrador.nombre, "correo": administrador.correo},
        "perfil": {
            "nombre_negocio": negocio.nombre_negocio,
            "direccion": negocio.direccion,
            "telefono": negocio.telefono,
            "horario": negocio.horario,
        },
        "contexto": {
            "servicios": n_servicios,
            "reglas": bool(contexto and (contexto.reglas_negocio or "").strip()),
            "instrucciones": bool(contexto and (contexto.instrucciones or "").strip()),
            "agendar_automatico": bool(contexto and contexto.agendar_automatico),
            "proveedor": contexto.ai_provider if contexto else None,
            "modelo": contexto.ai_model if contexto else None,
        },
        "bot_activo": bool(negocio.estado_bot),
        "whatsapp_conectado": bool(negocio.whatsapp_phone_number_id and negocio.whatsapp_token),
        "historial": {
            "conversaciones": n_conversaciones,
            "mensajes_cliente": n_cliente,
            "respuestas_bot": n_bot,
            "respuestas_ia": n_ia,
            "respuestas_sin_ia": n_sin_ia,
            "ultimo_mensaje": ultimo_cliente.fecha_hora.isoformat() if ultimo_cliente and ultimo_cliente.fecha_hora else None,
        },
        "latencia": {
            "total": len(latencias),
            "limite_s": MAX_RESPONSE_LATENCY_SECONDS,
            "promedio_s": round(sum(latencias) / len(latencias) / 1000, 2) if latencias else None,
            "maximo_s": round(max(latencias) / 1000, 2) if latencias else None,
            "dentro_del_limite_pct": round(100 * sum(1 for l in latencias if l <= limite_ms) / len(latencias), 1) if latencias else None,
        },
        "agenda": {
            "citas_activas": len(activas),
            "clientes_con_varias_citas": sum(1 for n in por_cliente.values() if n > 1),
            "total_citas": citas.count(),
            "canceladas": citas.filter(Agenda.estado_cita == "cancelada").count(),
        },
        # Solo el id (sin datos): la demo intenta leerlo y debe recibir 403
        "id_otro_negocio": otro[0] if otro else None,
        "servidor_hora_utc": datetime.utcnow().isoformat(timespec="seconds"),
    }
