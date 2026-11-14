"""
Reglas de agenda: bloqueo de horarios y expiración de citas pendientes.

Estas funciones son la ÚNICA fuente de verdad para saber si un horario está
libre. Las usa tanto el Dashboard (agendado manual) como, cuando se active
"Agendar cita automática", el flujo de WhatsApp/IA: así ambas vías aplican
exactamente las mismas reglas.

Reglas:
- Una cita ocupa el intervalo [hora_cita, hora_cita + duración del servicio).
  Si el servicio no tiene duración, se asume DURACION_POR_DEFECTO_MIN.
- Bloquean el horario las citas "confirmada", "completada" y las "pendiente"
  que todavía no vencieron.
- Una cita "pendiente" vence a las HORAS_VENCIMIENTO_PENDIENTE desde que se
  creó; al vencer pasa a "expirada" y el horario queda libre.
- Las "cancelada" y "expirada" nunca bloquean.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.agenda import Agenda
from app.models.servicio import Servicio
from app.services.horario_service import validar_dentro_de_horario

HORAS_VENCIMIENTO_PENDIENTE = 2
DURACION_POR_DEFECTO_MIN = 30
ESTADOS_QUE_BLOQUEAN = ("pendiente", "confirmada", "completada")


class HorarioOcupadoError(Exception):
    """El intervalo pedido se solapa con otra cita activa."""

    def __init__(self, conflicto_inicio: time, conflicto_fin: time):
        self.conflicto_inicio = conflicto_inicio
        self.conflicto_fin = conflicto_fin
        super().__init__(
            f"Ese horario ya está ocupado (hay una cita de "
            f"{conflicto_inicio:%H:%M} a {conflicto_fin:%H:%M})"
        )


class CitaActivaExistenteError(Exception):
    """El cliente ya tiene una cita pendiente o confirmada en el negocio."""

    def __init__(self, cita: Agenda, servicio: Servicio):
        self.cita = cita
        self.servicio = servicio
        super().__init__(
            f"Ya tienes una cita activa de {servicio.nombre_servicio} el "
            f"{cita.fecha_cita:%d-%m} a las {cita.hora_cita:%H:%M}."
        )


class SinCitaActivaError(Exception):
    """El cliente intentó reagendar o cancelar sin tener una cita activa."""


def ahora_utc() -> datetime:
    """Hora actual en UTC, sin tzinfo (así se guarda en la BD)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def duracion_minutos(servicio: Servicio) -> int:
    return servicio.duracion_estimada or DURACION_POR_DEFECTO_MIN


def _intervalo(fecha: date, hora: time, minutos: int) -> tuple[datetime, datetime]:
    inicio = datetime.combine(fecha, hora)
    return inicio, inicio + timedelta(minutes=minutos)


def liberar_pendientes_vencidas(db: Session, id_negocio: int) -> int:
    limite = ahora_utc() - timedelta(hours=HORAS_VENCIMIENTO_PENDIENTE)
    ids_servicios = db.query(Servicio.id_servicio).filter(Servicio.id_negocio == id_negocio)
    vencidas = (
        db.query(Agenda)
        .filter(
            Agenda.id_servicio.in_(ids_servicios),
            Agenda.estado_cita == "pendiente",
            Agenda.fecha_creacion <= limite,
        )
        .all()
    )
    for cita in vencidas:
        cita.estado_cita = "expirada"
    if vencidas:
        db.commit()
    return len(vencidas)


def obtener_cita_activa_cliente(
    db: Session, id_negocio: int, id_cliente: int
) -> tuple[Agenda, Servicio] | None:
    """Devuelve la cita 'pendiente' o 'confirmada' más próxima del cliente."""
    liberar_pendientes_vencidas(db, id_negocio)
    return (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(
            Servicio.id_negocio == id_negocio,
            Agenda.id_cliente == id_cliente,
            Agenda.estado_cita.in_(("pendiente", "confirmada")),
        )
        .order_by(Agenda.fecha_cita.asc(), Agenda.hora_cita.asc())
        .first()
    )


def validar_horario_libre(
    db: Session,
    id_negocio: int,
    fecha: date,
    hora: time,
    duracion_min: int,
    excluir_id_agenda: int | None = None,
    excluir_ids_agenda: set[int] | None = None,
) -> None:
    liberar_pendientes_vencidas(db, id_negocio)

    nuevo_ini, nuevo_fin = _intervalo(fecha, hora, duracion_min)

    consulta = (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(
            Servicio.id_negocio == id_negocio,
            Agenda.fecha_cita == fecha,
            Agenda.estado_cita.in_(ESTADOS_QUE_BLOQUEAN),
        )
    )
    ids_excluir = set(excluir_ids_agenda or ())
    if excluir_id_agenda is not None:
        ids_excluir.add(excluir_id_agenda)
    if ids_excluir:
        consulta = consulta.filter(~Agenda.id_agenda.in_(ids_excluir))

    for cita, servicio in consulta.all():
        ini, fin = _intervalo(cita.fecha_cita, cita.hora_cita, duracion_minutos(servicio))
        if nuevo_ini < fin and ini < nuevo_fin:
            raise HorarioOcupadoError(ini.time(), fin.time())


def crear_cita_validada(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    servicio: Servicio,
    fecha: date,
    hora: time,
    horario_negocio: str | None = None,
    permitir_duplicado_cliente: bool = True,
) -> Agenda:
    """Crea una cita 'pendiente'. Desde el flujo IA pasa permitir_duplicado_cliente=False
    para evitar que se dupliquen reservas cuando el cliente pide un cambio."""
    if not permitir_duplicado_cliente:
        existente = obtener_cita_activa_cliente(db, id_negocio, id_cliente)
        if existente is not None:
            raise CitaActivaExistenteError(*existente)

    validar_dentro_de_horario(horario_negocio, fecha, hora, duracion_minutos(servicio))
    validar_horario_libre(db, id_negocio, fecha, hora, duracion_minutos(servicio))

    cita = Agenda(
        id_cliente=id_cliente,
        id_servicio=servicio.id_servicio,
        fecha_cita=fecha,
        hora_cita=hora,
        estado_cita="pendiente",
        fecha_creacion=ahora_utc(),
    )
    db.add(cita)
    db.commit()
    db.refresh(cita)
    return cita


def reagendar_cita_validada(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    nueva_fecha: date,
    nueva_hora: time,
    nuevo_servicio: Servicio | None = None,
    horario_negocio: str | None = None,
) -> Agenda:
    """Mueve la cita activa más reciente del cliente a un nuevo día/hora
    y cancela cualquier duplicado activo previo."""
    liberar_pendientes_vencidas(db, id_negocio)
    activas = (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(
            Servicio.id_negocio == id_negocio,
            Agenda.id_cliente == id_cliente,
            Agenda.estado_cita.in_(("pendiente", "confirmada")),
        )
        .order_by(Agenda.fecha_cita.desc(), Agenda.hora_cita.desc())
        .all()
    )
    if not activas:
        raise SinCitaActivaError("No tienes una cita activa para reagendar.")

    cita, servicio_actual = activas[0]
    servicio_final = nuevo_servicio or servicio_actual
    duracion = duracion_minutos(servicio_final)
    ids_cliente = {c.id_agenda for c, _ in activas}

    validar_dentro_de_horario(horario_negocio, nueva_fecha, nueva_hora, duracion)
    validar_horario_libre(
        db,
        id_negocio,
        nueva_fecha,
        nueva_hora,
        duracion,
        excluir_ids_agenda=ids_cliente,
    )

    for extra, _ in activas[1:]:
        extra.estado_cita = "cancelada"

    cita.id_servicio = servicio_final.id_servicio
    cita.fecha_cita = nueva_fecha
    cita.hora_cita = nueva_hora
    cita.estado_cita = "pendiente"
    cita.fecha_creacion = ahora_utc()
    db.commit()
    db.refresh(cita)
    return cita


def cancelar_cita_activa(
    db: Session, id_negocio: int, id_cliente: int
) -> Agenda:
    """Cancela la cita activa del cliente y libera el bloque horario."""
    existente = obtener_cita_activa_cliente(db, id_negocio, id_cliente)
    if existente is None:
        raise SinCitaActivaError("No tienes una cita activa para cancelar.")

    cita, _ = existente
    cita.estado_cita = "cancelada"
    db.commit()
    db.refresh(cita)
    return cita
