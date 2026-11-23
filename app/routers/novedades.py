"""Actualización en vivo del Dashboard.

El Dashboard consulta GET /negocio/{id}/novedades cada pocos segundos. La
respuesta es liviana: dos "firmas" (si cambian, el Dashboard recarga la
Agenda o las Conversaciones) y los avisos sin leer. Se usa polling en vez de
WebSockets porque en Vercel (serverless) no hay conexiones persistentes.

De paso, ejecuta los recordatorios del negocio (como mucho 1 vez por minuto),
para que funcionen aunque el cron externo se atrase.
"""
import hashlib

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import obtener_negocio_propio
from app.db.database import get_db
from app.models.agenda import Agenda
from app.models.aviso import Aviso
from app.models.conversacion import Conversacion, Mensaje
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import recordatorios

router = APIRouter(prefix="/negocio/{id_negocio}", tags=["Novedades (en vivo)"])


class AvisoResponse(BaseModel):
    id_aviso: int
    tipo: str
    texto: str
    fecha: str
    id_agenda: int | None


class LeerAvisosRequest(BaseModel):
    ids: list[int] | None = None  # None = marcar todos


def _firma_agenda(db: Session, id_negocio: int) -> str:
    filas = (
        db.query(
            Agenda.id_agenda, Agenda.estado_cita, Agenda.fecha_cita, Agenda.hora_cita,
            Agenda.respuesta_cliente, Agenda.recordatorio_24h_enviado, Agenda.confirmacion_1h_enviada,
            Agenda.nombre_contacto, Agenda.correo_contacto, Agenda.telefono_contacto,
        )
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(Servicio.id_negocio == id_negocio)
        .order_by(Agenda.id_agenda)
        .all()
    )
    return hashlib.md5(repr(filas).encode()).hexdigest()[:16]


def _firma_conversaciones(db: Session, id_negocio: int) -> str:
    total, ultimo = (
        db.query(func.count(Mensaje.id_mensaje), func.max(Mensaje.id_mensaje))
        .join(Conversacion, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .filter(Conversacion.id_negocio == id_negocio)
        .one()
    )
    return f"{total}-{ultimo or 0}"


@router.get("/novedades")
def obtener_novedades(db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)):
    recordatorios.ejecutar_si_toca(db, negocio.id_negocio)
    avisos = (
        db.query(Aviso)
        .filter(Aviso.id_negocio == negocio.id_negocio, Aviso.leido.is_(False))
        .order_by(Aviso.id_aviso.desc())
        .limit(20)
        .all()
    )
    return {
        "firma_agenda": _firma_agenda(db, negocio.id_negocio),
        "firma_conversaciones": _firma_conversaciones(db, negocio.id_negocio),
        "avisos": [
            AvisoResponse(
                id_aviso=a.id_aviso, tipo=a.tipo, texto=a.texto,
                fecha=a.fecha.isoformat() + "Z", id_agenda=a.id_agenda,
            )
            for a in avisos
        ],
    }


@router.post("/avisos/leer")
def marcar_avisos_leidos(
    datos: LeerAvisosRequest,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    consulta = db.query(Aviso).filter(Aviso.id_negocio == negocio.id_negocio, Aviso.leido.is_(False))
    if datos.ids:
        consulta = consulta.filter(Aviso.id_aviso.in_(datos.ids))
    marcados = consulta.update({Aviso.leido: True}, synchronize_session=False)
    db.commit()
    return {"marcados": marcados}
