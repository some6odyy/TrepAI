"""Horarios bloqueados del negocio: vacaciones, feriados, almuerzo. Ni el
Dashboard ni el bot pueden agendar en ellos."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import obtener_negocio_propio
from app.core.tiempo import ahora_chile_naive
from app.db.database import get_db
from app.models.agenda import Agenda
from app.models.bloqueo import Bloqueo
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.schemas.bloqueo import BloqueoCreate, BloqueoResponse
from app.services import agenda_service, bloqueo_service

router = APIRouter(prefix="/negocio/{id_negocio}/bloqueos", tags=["Bloqueos de horario"])

# Una cita "dentro" de un bloqueo: se revisan hasta este número de días hacia adelante
DIAS_REVISION = 180


def _citas_afectadas(db: Session, id_negocio: int, bloqueo: Bloqueo) -> int:
    hoy = ahora_chile_naive().date()
    activas = (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(
            Servicio.id_negocio == id_negocio,
            Agenda.estado_cita.in_(("pendiente", "confirmada")),
            Agenda.fecha_cita >= hoy,
            Agenda.fecha_cita <= hoy + timedelta(days=DIAS_REVISION),
        )
        .all()
    )
    total = 0
    for cita, servicio in activas:
        if not bloqueo_service.aplica_el_dia(bloqueo, cita.fecha_cita):
            continue
        ini = datetime.combine(cita.fecha_cita, cita.hora_cita)
        fin = ini + timedelta(minutes=agenda_service.duracion_minutos(servicio))
        if bloqueo.hora_desde is None:
            total += 1
        elif ini < datetime.combine(cita.fecha_cita, bloqueo.hora_hasta) and datetime.combine(cita.fecha_cita, bloqueo.hora_desde) < fin:
            total += 1
    return total


def _a_response(db: Session, bloqueo: Bloqueo, contar: bool = False) -> BloqueoResponse:
    return BloqueoResponse(
        id_bloqueo=bloqueo.id_bloqueo,
        tipo=bloqueo.tipo,
        fecha_desde=bloqueo.fecha_desde,
        fecha_hasta=bloqueo.fecha_hasta,
        hora_desde=bloqueo.hora_desde,
        hora_hasta=bloqueo.hora_hasta,
        dias_semana=sorted(bloqueo_service.dias_de(bloqueo)),
        motivo=bloqueo.motivo,
        descripcion=bloqueo_service.describir(bloqueo),
        citas_afectadas=_citas_afectadas(db, bloqueo.id_negocio, bloqueo) if contar else 0,
    )


@router.get("", response_model=list[BloqueoResponse])
def listar_bloqueos(db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)):
    """Bloqueos vigentes (los de fechas ya pasadas no se muestran)."""
    hoy = ahora_chile_naive().date()
    bloqueos = (
        db.query(Bloqueo)
        .filter(Bloqueo.id_negocio == negocio.id_negocio, or_(Bloqueo.fecha_hasta.is_(None), Bloqueo.fecha_hasta >= hoy))
        .order_by(Bloqueo.tipo.desc(), Bloqueo.fecha_desde, Bloqueo.hora_desde)
        .all()
    )
    return [_a_response(db, b, contar=True) for b in bloqueos]


@router.post("", response_model=BloqueoResponse, status_code=status.HTTP_201_CREATED)
def crear_bloqueo(
    datos: BloqueoCreate, db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)
):
    bloqueo = Bloqueo(
        id_negocio=negocio.id_negocio,
        tipo=datos.tipo,
        fecha_desde=datos.fecha_desde,
        fecha_hasta=datos.fecha_hasta,
        hora_desde=datos.hora_desde,
        hora_hasta=datos.hora_hasta,
        dias_semana=",".join(str(d) for d in datos.dias_semana) or None,
        motivo=(datos.motivo or "").strip() or None,
    )
    db.add(bloqueo)
    db.commit()
    db.refresh(bloqueo)
    return _a_response(db, bloqueo, contar=True)


@router.delete("/{id_bloqueo}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_bloqueo(
    id_bloqueo: int, db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)
):
    bloqueo = db.query(Bloqueo).filter(Bloqueo.id_bloqueo == id_bloqueo, Bloqueo.id_negocio == negocio.id_negocio).first()
    if bloqueo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bloqueo no encontrado")
    db.delete(bloqueo)
    db.commit()
