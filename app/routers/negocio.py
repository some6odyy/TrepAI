"""RF-02 | Gestión de perfil del negocio. RF-04 | Control on/off del bot."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.deps import obtener_administrador_actual, obtener_negocio_propio
from app.core.security import cifrar_texto
from app.models.administrador import Administrador
from app.models.negocio import Negocio
from app.schemas.negocio import NegocioCreate, NegocioUpdate, NegocioResponse, EstadoBotResponse
from app.schemas.whatsapp import WhatsAppConexionUpdate, WhatsAppEstadoResponse

router = APIRouter(prefix="/negocio", tags=["Negocio"])


@router.post("", response_model=NegocioResponse, status_code=status.HTTP_201_CREATED)
def crear_negocio(
    datos: NegocioCreate,
    db: Session = Depends(get_db),
    administrador_actual: Administrador = Depends(obtener_administrador_actual),
):
    """Alta de un negocio (ej. la barbería) bajo el administrador logueado."""
    nuevo_negocio = Negocio(
        id_administrador=administrador_actual.id_administrador,
        nombre_negocio=datos.nombre_negocio,
        direccion=datos.direccion,
        telefono=datos.telefono,
        horario=datos.horario,
        estado_bot=False,
    )
    db.add(nuevo_negocio)
    db.commit()
    db.refresh(nuevo_negocio)
    return nuevo_negocio


@router.get("", response_model=list[NegocioResponse])
def listar_mis_negocios(
    db: Session = Depends(get_db),
    administrador_actual: Administrador = Depends(obtener_administrador_actual),
):
    """Devuelve solo los negocios del administrador logueado."""
    return (
        db.query(Negocio)
        .filter(Negocio.id_administrador == administrador_actual.id_administrador)
        .all()
    )


@router.get("/{id_negocio}", response_model=NegocioResponse)
def obtener_negocio(negocio: Negocio = Depends(obtener_negocio_propio)):
    return negocio


@router.put("/{id_negocio}/perfil", response_model=NegocioResponse)
def actualizar_perfil(
    datos: NegocioUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Actualiza solo los campos enviados en el body."""
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(negocio, campo, valor)

    db.commit()
    db.refresh(negocio)
    return negocio


@router.patch("/{id_negocio}/estado-bot", response_model=EstadoBotResponse)
def cambiar_estado_bot(
    activo: bool,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-04 — interruptor inmediato para prender/apagar el bot."""
    negocio.estado_bot = activo
    db.commit()
    db.refresh(negocio)
    return EstadoBotResponse(id_negocio=negocio.id_negocio, estado_bot=negocio.estado_bot)


@router.get("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def obtener_estado_whatsapp(negocio: Negocio = Depends(obtener_negocio_propio)):
    return WhatsAppEstadoResponse(
        conectado=bool(negocio.whatsapp_phone_number_id and negocio.whatsapp_token),
        phone_number_id=negocio.whatsapp_phone_number_id,
    )


@router.put("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def conectar_whatsapp(
    datos: WhatsAppConexionUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-06 — guarda las credenciales del número de WhatsApp Business
    Cloud API del negocio (obtenidas en developers.facebook.com, modo
    sandbox mientras la app no esté verificada por Meta)."""
    ya_usado = (
        negocio.whatsapp_phone_number_id != datos.phone_number_id
        and db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == datos.phone_number_id).first()
    )
    if ya_usado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese phone_number_id ya está conectado a otro negocio",
        )

    negocio.whatsapp_phone_number_id = datos.phone_number_id
    negocio.whatsapp_token = cifrar_texto(datos.access_token)
    db.commit()
    db.refresh(negocio)

    return WhatsAppEstadoResponse(conectado=True, phone_number_id=negocio.whatsapp_phone_number_id)


@router.delete("/{id_negocio}/whatsapp", response_model=WhatsAppEstadoResponse)
def desvincular_whatsapp(
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """RF-06 — desvincula el número de WhatsApp del negocio.

    Borra el phone_number_id y el token cifrado de la BD, y apaga el bot
    (RF-04) para que no quede "encendido" sin un número por el cual
    responder. Desde este momento el webhook ignora los mensajes que
    lleguen a ese número (no encuentra a qué negocio pertenecen), y el
    phone_number_id queda libre para conectarlo a otro negocio.

    Ojo: esto solo corta la conexión del lado de TrepAI. El número sigue
    registrado en la app de Meta (developers.facebook.com); si se quiere
    dar de baja allá también, se hace desde ese panel.

    Es idempotente: desvincular un negocio que no tenía número conectado
    simplemente devuelve conectado=False."""
    negocio.whatsapp_phone_number_id = None
    negocio.whatsapp_token = None
    negocio.estado_bot = False
    db.commit()
    db.refresh(negocio)

    return WhatsAppEstadoResponse(conectado=False, phone_number_id=None)
