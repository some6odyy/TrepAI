"""
Puente de DEMOSTRACIÓN para WhatsApp (alternativa a la Cloud API de Meta).

El script de bridge/ (Node + whatsapp-web.js) vincula un número normal de
WhatsApp escaneando un QR, escucha los mensajes y los reenvía a este
endpoint. Aquí se procesan con exactamente la misma lógica que el webhook
oficial (chat_service) y se devuelve el texto que el bridge debe responder.

Seguridad: el endpoint queda DESACTIVADO si PUENTE_TOKEN no está definido
en el .env, así que en producción (Vercel) no existe a menos que alguien
lo active a propósito. El bridge manda el token en el header X-Puente-Token.

El diseño oficial del sistema sigue siendo la WhatsApp Business Cloud API
(RF-06 / RF-09); esto es solo para demos y desarrollo local.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import PUENTE_TOKEN, PUENTE_ID_NEGOCIO
from app.db.database import get_db
from app.models.negocio import Negocio
from app.services import chat_service

router = APIRouter(prefix="/puente", tags=["Puente WhatsApp (demo)"])


class MensajePuente(BaseModel):
    telefono: str = Field(min_length=5, description="Número del cliente, solo dígitos con código país (ej. 56912345678)")
    mensaje: str = Field(min_length=1, max_length=4000)


def _verificar_token(x_puente_token: str = Header(default="")):
    if not PUENTE_TOKEN:
        raise HTTPException(status_code=404, detail="Puente desactivado (define PUENTE_TOKEN en el .env)")
    if not secrets.compare_digest(x_puente_token, PUENTE_TOKEN):
        raise HTTPException(status_code=401, detail="Token del puente inválido")


def _negocio_del_puente(db: Session) -> Negocio | None:
    """Qué negocio atiende el puente: el de PUENTE_ID_NEGOCIO si está
    definido; si no, el primer negocio que tenga el bot encendido."""
    if PUENTE_ID_NEGOCIO:
        return db.query(Negocio).filter(Negocio.id_negocio == PUENTE_ID_NEGOCIO).first()
    return (
        db.query(Negocio)
        .filter(Negocio.estado_bot.is_(True))
        .order_by(Negocio.id_negocio)
        .first()
    )


@router.get("/estado", dependencies=[Depends(_verificar_token)])
def estado_puente(db: Session = Depends(get_db)):
    """El bridge lo llama al arrancar para mostrar qué negocio va a atender."""
    negocio = _negocio_del_puente(db)
    if negocio is None:
        return {"ok": False, "motivo": "No hay negocio con el bot encendido (o PUENTE_ID_NEGOCIO no existe)"}
    return {
        "ok": True,
        "id_negocio": negocio.id_negocio,
        "nombre_negocio": negocio.nombre_negocio,
        "bot_activo": bool(negocio.estado_bot),
    }


@router.post("/mensaje", dependencies=[Depends(_verificar_token)])
def recibir_mensaje_puente(datos: MensajePuente, db: Session = Depends(get_db)):
    negocio = _negocio_del_puente(db)
    if negocio is None:
        return {"status": "negocio_no_encontrado", "respuesta": None}

    # RF-04: si el administrador pausó el bot desde el Dashboard, no respondemos
    if not negocio.estado_bot:
        return {"status": "bot_inactivo", "respuesta": None}

    telefono = "".join(c for c in datos.telefono if c.isdigit())
    respuesta = chat_service.procesar_mensaje(db, negocio, telefono, datos.mensaje.strip())
    return {"status": "procesado", "respuesta": respuesta}
