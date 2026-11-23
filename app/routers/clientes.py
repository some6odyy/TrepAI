"""Vista Clientes: una ficha por cliente del negocio con sus citas,
inasistencias y las notas del barbero."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import obtener_negocio_propio
from app.core.tiempo import ahora_chile_naive
from app.db.database import get_db
from app.models.agenda import Agenda
from app.models.cliente import Cliente
from app.models.conversacion import Conversacion, Mensaje
from app.models.ficha_cliente import FichaCliente
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.schemas.cliente import CitaDeCliente, ClienteDetalle, ClienteResumen, NotasUpdate
from app.services import agenda_service

router = APIRouter(prefix="/negocio/{id_negocio}/clientes", tags=["Clientes"])


def _datos_negocio(db: Session, id_negocio: int, id_cliente: int | None = None):
    """Clientes que escribieron o agendaron en este negocio, con sus citas."""
    agenda_service.liberar_pendientes_vencidas(db, id_negocio)
    citas_q = (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(Servicio.id_negocio == id_negocio)
    )
    actividad_q = (
        db.query(Conversacion.id_cliente, func.max(Mensaje.fecha_hora), func.max(Conversacion.id_conversacion))
        .outerjoin(Mensaje, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .filter(Conversacion.id_negocio == id_negocio)
        .group_by(Conversacion.id_cliente)
    )
    if id_cliente is not None:
        citas_q = citas_q.filter(Agenda.id_cliente == id_cliente)
        actividad_q = actividad_q.filter(Conversacion.id_cliente == id_cliente)

    citas_por_cliente: dict[int, list] = {}
    for cita, servicio in citas_q.order_by(Agenda.fecha_cita.desc(), Agenda.hora_cita.desc()).all():
        citas_por_cliente.setdefault(cita.id_cliente, []).append((cita, servicio))
    actividad = {fila[0]: (fila[1], fila[2]) for fila in actividad_q.all()}

    ids = set(citas_por_cliente) | set(actividad)
    if not ids:
        return [], {}, {}, {}
    clientes = db.query(Cliente).filter(Cliente.id_cliente.in_(ids)).all()
    notas = dict(
        db.query(FichaCliente.id_cliente, FichaCliente.notas)
        .filter(FichaCliente.id_negocio == id_negocio, FichaCliente.id_cliente.in_(ids))
        .all()
    )
    return clientes, citas_por_cliente, actividad, notas


def _resumen(cliente: Cliente, lista_citas: list, actividad: tuple | None, notas: str | None, cls=ClienteResumen, **extra):
    ahora = ahora_chile_naive()
    a_cita = lambda c, s: CitaDeCliente(  # noqa: E731
        id_agenda=c.id_agenda, fecha_cita=c.fecha_cita, hora_cita=c.hora_cita,
        nombre_servicio=s.nombre_servicio, estado_cita=c.estado_cita,
    )
    proximas = sorted(
        (
            (c, s) for c, s in lista_citas
            if c.estado_cita in ("pendiente", "confirmada") and agenda_service.inicio_cita(c) >= ahora
        ),
        key=lambda par: agenda_service.inicio_cita(par[0]),
    )
    completadas = [c for c, _ in lista_citas if c.estado_cita == "completada"]
    # El nombre y correo más recientes que dio al agendar, si la ficha no los tiene
    ultima_con_datos = next((c for c, _ in lista_citas if c.nombre_contacto or c.correo_contacto), None)
    return cls(
        id_cliente=cliente.id_cliente,
        nombre=cliente.nombre_cliente or (ultima_con_datos.nombre_contacto if ultima_con_datos else None),
        telefono=cliente.telefono,
        correo=cliente.correo or (ultima_con_datos.correo_contacto if ultima_con_datos else None),
        total_citas=len(lista_citas),
        completadas=len(completadas),
        inasistencias=sum(1 for c, _ in lista_citas if c.estado_cita == "no_asistio"),
        canceladas=sum(1 for c, _ in lista_citas if c.estado_cita == "cancelada"),
        ultima_visita=max((c.fecha_cita for c in completadas), default=None),
        proxima_cita=a_cita(*proximas[0]) if proximas else None,
        ultima_actividad=actividad[0] if actividad else None,
        notas=notas,
        **extra,
    )


@router.get("", response_model=list[ClienteResumen])
def listar_clientes(
    buscar: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    clientes, citas, actividad, notas = _datos_negocio(db, negocio.id_negocio)
    lista = [
        _resumen(c, citas.get(c.id_cliente, []), actividad.get(c.id_cliente), notas.get(c.id_cliente))
        for c in clientes
    ]
    if buscar:
        q = buscar.strip().lower()
        digitos = "".join(ch for ch in q if ch.isdigit())
        lista = [
            c for c in lista
            if q in (c.nombre or "").lower() or q in (c.correo or "").lower() or (digitos and digitos in c.telefono)
        ]
    # Primero los con actividad más reciente (mensaje o cita)
    def reciente(c: ClienteResumen):
        fechas = [c.ultima_actividad] if c.ultima_actividad else []
        if c.proxima_cita:
            fechas.append(datetime.combine(c.proxima_cita.fecha_cita, c.proxima_cita.hora_cita))
        elif c.ultima_visita:
            fechas.append(datetime.combine(c.ultima_visita, datetime.min.time()))
        return max(fechas, default=datetime.min)
    return sorted(lista, key=reciente, reverse=True)


def _detalle(db: Session, negocio: Negocio, id_cliente: int) -> ClienteDetalle:
    clientes, citas, actividad, notas = _datos_negocio(db, negocio.id_negocio, id_cliente)
    if not clientes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado en este negocio")
    lista = citas.get(id_cliente, [])
    return _resumen(
        clientes[0], lista, actividad.get(id_cliente), notas.get(id_cliente), cls=ClienteDetalle,
        citas=[
            CitaDeCliente(
                id_agenda=c.id_agenda, fecha_cita=c.fecha_cita, hora_cita=c.hora_cita,
                nombre_servicio=s.nombre_servicio, estado_cita=c.estado_cita,
            )
            for c, s in lista
        ],
        id_conversacion=actividad[id_cliente][1] if id_cliente in actividad else None,
    )


@router.get("/{id_cliente}", response_model=ClienteDetalle)
def obtener_cliente(id_cliente: int, db: Session = Depends(get_db), negocio: Negocio = Depends(obtener_negocio_propio)):
    return _detalle(db, negocio, id_cliente)


@router.put("/{id_cliente}/notas", response_model=ClienteDetalle)
def guardar_notas(
    id_cliente: int,
    datos: NotasUpdate,
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    _detalle(db, negocio, id_cliente)  # 404 si no es cliente de este negocio
    ficha = (
        db.query(FichaCliente)
        .filter(FichaCliente.id_negocio == negocio.id_negocio, FichaCliente.id_cliente == id_cliente)
        .first()
    )
    if ficha is None:
        ficha = FichaCliente(id_negocio=negocio.id_negocio, id_cliente=id_cliente)
        db.add(ficha)
    ficha.notas = datos.notas.strip() or None
    ficha.fecha_actualizacion = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return _detalle(db, negocio, id_cliente)
