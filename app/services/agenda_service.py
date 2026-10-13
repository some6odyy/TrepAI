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


def ahora_utc() -> datetime:
    """Hora actual en UTC, sin tzinfo (así se guarda en la BD)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def duracion_minutos(servicio: Servicio) -> int:
    return servicio.duracion_estimada or DURACION_POR_DEFECTO_MIN


def _intervalo(fecha: date, hora: time, minutos: int) -> tuple[datetime, datetime]:
    inicio = datetime.combine(fecha, hora)
    return inicio, inicio + timedelta(minutes=minutos)


def liberar_pendientes_vencidas(db: Session, id_negocio: int) -> int:
    """Marca como 'expirada' toda cita 'pendiente' de este negocio creada hace
    más de HORAS_VENCIMIENTO_PENDIENTE horas. Devuelve cuántas liberó.

    Se ejecuta de forma perezosa (al listar, crear o cambiar estados) porque
    en Vercel no hay un proceso corriendo en segundo plano."""
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


def validar_horario_libre(
    db: Session,
    id_negocio: int,
    fecha: date,
    hora: time,
    duracion_min: int,
    excluir_id_agenda: int | None = None,
) -> None:
    """Lanza HorarioOcupadoError si [hora, hora+duración) choca con otra cita
    activa del mismo negocio. Libera antes las pendientes vencidas."""
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
    if excluir_id_agenda is not None:
        consulta = consulta.filter(Agenda.id_agenda != excluir_id_agenda)

    for cita, servicio in consulta.all():
        ini, fin = _intervalo(cita.fecha_cita, cita.hora_cita, duracion_minutos(servicio))
        # Intervalos semiabiertos: una cita que termina a las 10:30 NO choca
        # con otra que empieza a las 10:30.
        if nuevo_ini < fin and ini < nuevo_fin:
            raise HorarioOcupadoError(ini.time(), fin.time())


def crear_cita_validada(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    servicio: Servicio,
    fecha: date,
    hora: time,
) -> Agenda:
    """Crea una cita 'pendiente' si el horario está libre. Punto de entrada
    común para el Dashboard y para el futuro agendado automático por IA."""
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
