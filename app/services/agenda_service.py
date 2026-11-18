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
- No se puede agendar ni reagendar con menos de ANTICIPACION_MINIMA_HORAS
  de anticipación (hora de Chile).
- Toda cita nueva exige nombre, correo y teléfono de contacto: ahí se envían
  los recordatorios y la confirmación.
"""
import re
from datetime import date, datetime, time, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from app.core.tiempo import ahora_chile_naive, ahora_utc

from app.models.agenda import Agenda
from app.models.servicio import Servicio
from app.services.horario_service import validar_dentro_de_horario

HORAS_VENCIMIENTO_PENDIENTE = 2
DURACION_POR_DEFECTO_MIN = 30
ESTADOS_QUE_BLOQUEAN = ("pendiente", "confirmada", "completada")
ANTICIPACION_MINIMA_HORAS = 2


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


class AnticipacionInsuficienteError(Exception):
    """La cita empieza antes de ANTICIPACION_MINIMA_HORAS desde ahora."""

    def __init__(self, minimo: datetime, ya_paso: bool = False):
        self.minimo = minimo
        self.ya_paso = ya_paso
        if ya_paso:
            mensaje = "Esa hora ya pasó."
        else:
            mensaje = (
                f"Las citas se agendan con al menos {ANTICIPACION_MINIMA_HORAS} horas de "
                f"anticipación (desde las {minimo:%H:%M} del {minimo:%d-%m})."
            )
        super().__init__(mensaje)


class DatosContactoError(Exception):
    """Faltan o son inválidos el nombre, correo o teléfono de contacto."""

    def __init__(self, faltantes: list[str], invalidos: list[str] | None = None):
        self.faltantes = faltantes
        self.invalidos = invalidos or []
        partes = []
        if faltantes:
            partes.append("faltan: " + ", ".join(faltantes))
        if self.invalidos:
            partes.append("no son válidos: " + ", ".join(self.invalidos))
        super().__init__("Para agendar se necesitan nombre, correo y teléfono (" + "; ".join(partes) + ").")


def normalizar_telefono(telefono: str | None) -> str | None:
    """'+56 9 1234 5678' / '912345678' -> '56912345678'. None si no parece un
    número de WhatsApp válido (8 a 15 dígitos con código de país)."""
    digitos = re.sub(r"\D", "", telefono or "")
    if len(digitos) == 9 and digitos.startswith("9"):
        digitos = "56" + digitos
    if not 10 <= len(digitos) <= 15:
        return None
    return digitos


def normalizar_correo(correo: str | None) -> str | None:
    try:
        return validate_email((correo or "").strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def validar_contacto(nombre: str | None, correo: str | None, telefono: str | None) -> tuple[str, str, str]:
    """Devuelve (nombre, correo, telefono) normalizados o lanza DatosContactoError."""
    faltantes, invalidos = [], []
    nombre_ok = (nombre or "").strip()
    if len(nombre_ok) < 2:
        faltantes.append("nombre")
    correo_ok = None
    if not (correo or "").strip():
        faltantes.append("correo")
    else:
        correo_ok = normalizar_correo(correo)
        if correo_ok is None:
            invalidos.append("correo")
    telefono_ok = None
    if not (telefono or "").strip():
        faltantes.append("teléfono")
    else:
        telefono_ok = normalizar_telefono(telefono)
        if telefono_ok is None:
            invalidos.append("teléfono")
    if faltantes or invalidos:
        raise DatosContactoError(faltantes, invalidos)
    return nombre_ok[:100], correo_ok, telefono_ok


def validar_anticipacion(fecha: date, hora: time, ahora_local: datetime | None = None) -> None:
    """Rechaza citas que empiezan en menos de ANTICIPACION_MINIMA_HORAS."""
    ahora = (ahora_local or ahora_chile_naive()).replace(tzinfo=None)
    inicio = datetime.combine(fecha, hora)
    minimo = ahora + timedelta(hours=ANTICIPACION_MINIMA_HORAS)
    if inicio < minimo:
        raise AnticipacionInsuficienteError(minimo, ya_paso=inicio <= ahora)


def inicio_cita(cita: Agenda) -> datetime:
    return datetime.combine(cita.fecha_cita, cita.hora_cita)


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
    nombre_contacto: str | None = None,
    correo_contacto: str | None = None,
    telefono_contacto: str | None = None,
    ahora_local: datetime | None = None,
) -> Agenda:
    """Crea una cita 'pendiente'. Desde el flujo IA pasa permitir_duplicado_cliente=False
    para evitar que se dupliquen reservas cuando el cliente pide un cambio.

    Exige nombre, correo y teléfono de contacto y al menos
    ANTICIPACION_MINIMA_HORAS de anticipación."""
    if not permitir_duplicado_cliente:
        existente = obtener_cita_activa_cliente(db, id_negocio, id_cliente)
        if existente is not None:
            raise CitaActivaExistenteError(*existente)

    nombre_contacto, correo_contacto, telefono_contacto = validar_contacto(
        nombre_contacto, correo_contacto, telefono_contacto
    )
    validar_anticipacion(fecha, hora, ahora_local)

    validar_dentro_de_horario(horario_negocio, fecha, hora, duracion_minutos(servicio))
    validar_horario_libre(db, id_negocio, fecha, hora, duracion_minutos(servicio))

    cita = Agenda(
        id_cliente=id_cliente,
        id_servicio=servicio.id_servicio,
        fecha_cita=fecha,
        hora_cita=hora,
        estado_cita="pendiente",
        fecha_creacion=ahora_utc(),
        nombre_contacto=nombre_contacto,
        correo_contacto=correo_contacto,
        telefono_contacto=telefono_contacto,
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
    ahora_local: datetime | None = None,
) -> Agenda:
    """Mueve la cita activa más reciente del cliente a un nuevo día/hora
    y cancela cualquier duplicado activo previo. Conserva los datos de
    contacto; los recordatorios se reprograman para la nueva hora."""
    validar_anticipacion(nueva_fecha, nueva_hora, ahora_local)
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
    # Nueva hora = nuevos recordatorios (el aviso de "cita agendada" se reenvía con la hora nueva)
    cita.aviso_reserva_enviado = None
    cita.recordatorio_24h_enviado = None
    cita.confirmacion_1h_enviada = None
    cita.respuesta_cliente = None
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
