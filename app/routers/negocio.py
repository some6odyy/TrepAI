"""RF-02 | Gestión de perfil del negocio. RF-04 | Control on/off del bot."""
import os
import re
import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import obtener_administrador_actual, obtener_negocio_propio
from app.core.security import cifrar_texto
from app.db.database import get_db
from app.models.administrador import Administrador
from app.models.negocio import Negocio
from app.schemas.negocio import EstadoBotResponse, NegocioCreate, NegocioResponse, NegocioUpdate
from app.schemas.whatsapp import WhatsAppConexionUpdate, WhatsAppEstadoResponse

router = APIRouter(prefix="/negocio", tags=["Negocio"])

GRAPH_URL = "https://graph.facebook.com/v26.0"


class SolicitarCodigoWhatsApp(BaseModel):
    telefono: str
    nombre_visible: str


class VerificarCodigoWhatsApp(BaseModel):
    codigo: str


def _separar_telefono_chile(telefono: str) -> tuple[str, str]:
    digitos = re.sub(r"\D", "", telefono)
    if digitos.startswith("56") and len(digitos) >= 11:
        return "56", digitos[2:]
    if len(digitos) == 9 and digitos.startswith("9"):
        return "56", digitos
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Ingresa un número válido con código de país (ej. +56912345678)",
    )


def _credenciales_maestras_meta() -> tuple[str, str]:
    waba_id = (os.getenv("META_WABA_ID") or "").strip()
    master_token = (os.getenv("META_MASTER_TOKEN") or "").strip()
    if not waba_id or not master_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Faltan META_WABA_ID o META_MASTER_TOKEN en la configuración del servidor",
        )
    return waba_id, master_token


@router.post("/{id_negocio}/whatsapp/solicitar-codigo")
def solicitar_codigo_whatsapp(
    datos: SolicitarCodigoWhatsApp,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    waba_id, master_token = _credenciales_maestras_meta()
    headers = {"Authorization": f"Bearer {master_token}"}
    cc, numero_local = _separar_telefono_chile(datos.telefono)

    # 1) Dar de alta el número en el WABA de TrepAI en Meta
    resp_num = requests.post(
        f"{GRAPH_URL}/{waba_id}/phone_numbers",
        headers=headers,
        json={
            "cc": cc,
            "phone_number": numero_local,
            "verified_name": datos.nombre_visible.strip(),
        },
        timeout=10,
    )
    if not resp_num.ok:
        detalle = resp_num.json().get("error", {}).get("message", "No se pudo registrar el número en Meta")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detalle)

    phone_number_id = resp_num.json().get("id")
    if not phone_number_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meta no devolvió el ID del número")

    ya_usado = (
        negocio.whatsapp_phone_number_id != phone_number_id
        and db.query(Negocio).filter(Negocio.whatsapp_phone_number_id == phone_number_id).first()
    )
    if ya_usado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese número ya está conectado a otro negocio en TrepAI",
        )

    # 2) Pedir a Meta que envíe el SMS con el código de 6 dígitos
    resp_sms = requests.post(
        f"{GRAPH_URL}/{phone_number_id}/request_code",
        headers=headers,
        json={"code_method": "SMS", "language": "es"},
        timeout=10,
    )
    if not resp_sms.ok:
        detalle = resp_sms.json().get("error", {}).get("message", "No se pudo enviar el SMS de verificación")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detalle)

    negocio.whatsapp_phone_number_id = phone_number_id
    db.commit()

    return {"ok": True, "phone_number_id": phone_number_id}


@router.post("/{id_negocio}/whatsapp/verificar-codigo", response_model=WhatsAppEstadoResponse)
def verificar_codigo_whatsapp(
    datos: VerificarCodigoWhatsApp,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    if not negocio.whatsapp_phone_number_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Primero solicita el código SMS con tu número",
        )

    _, master_token = _credenciales_maestras_meta()
    headers = {"Authorization": f"Bearer {master_token}"}
    phone_id = negocio.whatsapp_phone_number_id

    # 1) Validar código de 6 dígitos
    resp_verify = requests.post(
        f"{GRAPH_URL}/{phone_id}/verify_code",
        headers=headers,
        json={"code": re.sub(r"\D", "", datos.codigo)},
        timeout=10,
    )
    if not resp_verify.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código incorrecto o expirado")

    # 2) Registrar el número en Cloud API para habilitar mensajería
    resp_reg = requests.post(
        f"{GRAPH_URL}/{phone_id}/register",
        headers=headers,
        json={"messaging_product": "whatsapp", "pin": "123456"},
        timeout=10,
    )
    if not resp_reg.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código verificado, pero falló la activación en Cloud API",
        )

    negocio.whatsapp_token = cifrar_texto(master_token)
    negocio.estado_bot = True
    db.commit()
    db.refresh(negocio)

    return WhatsAppEstadoResponse(conectado=True, phone_number_id=negocio.whatsapp_phone_number_id)


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
    """RF-06 — guarda las credenciales del número de WhatsApp Business Cloud API."""
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
    """RF-06 — desvincula el número de WhatsApp del negocio."""
    negocio.whatsapp_phone_number_id = None
    negocio.whatsapp_token = None
    negocio.estado_bot = False
    db.commit()
    db.refresh(negocio)

    return WhatsAppEstadoResponse(conectado=False, phone_number_id=None)