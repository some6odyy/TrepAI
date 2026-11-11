"""
Agendado automático de citas desde el chat (interruptor "Agendar cita
automática" del bloque Contexto IA).

Cómo funciona:
1. Cuando el interruptor está encendido, el prompt le pide a la IA que, en
   cuanto el cliente haya dado servicio + fecha + hora, NO confirme la cita
   ella misma, sino que responda con un marcador:
       [[AGENDAR|Degradado|2026-11-12|16:00]]
2. Este módulo detecta el marcador, busca el servicio en el catálogo y crea
   la cita en estado "pendiente" con agenda_service.crear_cita_validada —
   la MISMA función que usa el agendado manual del Dashboard. Así se
   respetan el horario de atención, el bloqueo de horarios y el vencimiento
   de 2 horas sin duplicar reglas.
3. El mensaje final al cliente lo arma el backend según el resultado real
   (creada, horario ocupado, fuera de horario, etc.), para que la IA nunca
   diga "agendado" si en verdad no se pudo.

La cita queda "pendiente" hasta que el barbero la confirme en la Agenda.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.agenda import Agenda
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import agenda_service
from app.services.agenda_service import HorarioOcupadoError
from app.services.horario_service import FueraDeHorarioError, validar_dentro_de_horario

_RE_MARCADOR = re.compile(
    r"\[\[\s*AGENDAR\s*\|\s*(?P<servicio>[^|\]]+?)\s*\|\s*(?P<fecha>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<hora>\d{1,2}:\d{2})\s*\]\]",
    re.IGNORECASE,
)
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
PASO_SUGERENCIAS_MIN = 30
MAX_SUGERENCIAS = 3


@dataclass
class SolicitudCita:
    servicio: str
    fecha: date
    hora: time


def instrucciones_para_prompt() -> str:
    """Texto que se agrega al prompt cuando el agendado automático está activo."""
    return (
        "AGENDADO AUTOMÁTICO ACTIVADO: tú puedes reservar horas.\n"
        "- Para reservar necesitas: un servicio del catálogo, una fecha y una hora. "
        "Si falta alguno, pregúntalo.\n"
        "- Interpreta fechas relativas (\"mañana\", \"el jueves\") con la fecha actual. "
        "Si el día de la semana y el número del día no coinciden, pregunta cuál es el correcto.\n"
        "- Cuando tengas los tres datos y el cliente quiera reservar, responde ÚNICAMENTE con "
        "esta línea, sin ningún otro texto:\n"
        "[[AGENDAR|<nombre exacto del servicio como aparece en el catálogo>|<AAAA-MM-DD>|<HH:MM>]]\n"
        "  Ejemplo: [[AGENDAR|Degradado|2026-11-12|16:00]]\n"
        "- El sistema revisa la disponibilidad y le responde al cliente; nunca digas tú que la hora "
        "quedó reservada ni inventes disponibilidad."
    )


def extraer_solicitud(texto: str) -> tuple[str, SolicitudCita | None]:
    """Separa el marcador [[AGENDAR|...]] del texto de la IA.
    Devuelve (texto_sin_marcador, solicitud o None)."""
    coincidencia = _RE_MARCADOR.search(texto or "")
    if not coincidencia:
        return texto, None
    limpio = _RE_MARCADOR.sub("", texto).strip()
    try:
        fecha = date.fromisoformat(coincidencia["fecha"])
        hh, mm = coincidencia["hora"].split(":")
        hora = time(int(hh), int(mm))
    except ValueError:
        return limpio, None
    return limpio, SolicitudCita(coincidencia["servicio"].strip(), fecha, hora)


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def buscar_servicio(nombre: str, servicios: list[Servicio]) -> Servicio | None:
    """Coincidencia exacta (sin tildes ni mayúsculas) y, si no hay, parcial."""
    buscado = _normalizar(nombre)
    for s in servicios:
        if _normalizar(s.nombre_servicio) == buscado:
            return s
    parciales = [
        s for s in servicios
        if buscado in _normalizar(s.nombre_servicio) or _normalizar(s.nombre_servicio) in buscado
    ]
    return parciales[0] if len(parciales) == 1 else None


def _fecha_legible(fecha: date) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha:%d-%m}"


def _sugerir_horarios(db: Session, negocio: Negocio, servicio: Servicio, fecha: date, desde: time, ahora: datetime) -> list[time]:
    """Próximos horarios libres del mismo día, a partir de la hora pedida."""
    duracion = agenda_service.duracion_minutos(servicio)
    sugerencias: list[time] = []
    actual = datetime.combine(fecha, desde)
    fin_del_dia = datetime.combine(fecha, time(23, 59))
    while actual <= fin_del_dia and len(sugerencias) < MAX_SUGERENCIAS:
        if actual > ahora:
            try:
                validar_dentro_de_horario(negocio.horario, fecha, actual.time(), duracion)
                agenda_service.validar_horario_libre(db, negocio.id_negocio, fecha, actual.time(), duracion)
                sugerencias.append(actual.time())
            except (FueraDeHorarioError, HorarioOcupadoError):
                pass
        actual += timedelta(minutes=PASO_SUGERENCIAS_MIN)
    return sugerencias


def procesar_solicitud(
    db: Session,
    negocio: Negocio,
    id_cliente: int,
    solicitud: SolicitudCita,
    ahora_local: datetime,
) -> str:
    """Intenta crear la cita pendiente y devuelve el mensaje para el cliente."""
    servicios = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()
    servicio = buscar_servicio(solicitud.servicio, servicios)
    if servicio is None:
        nombres = ", ".join(s.nombre_servicio for s in servicios) or "ninguno cargado"
        return f"No encontré el servicio \"{solicitud.servicio}\". ¿Cuál de estos quieres? {nombres}"

    ahora = ahora_local.replace(tzinfo=None)
    inicio = datetime.combine(solicitud.fecha, solicitud.hora)
    cuando = f"*{_fecha_legible(solicitud.fecha)}* a las *{solicitud.hora:%H:%M}*"

    if inicio <= ahora:
        return f"Esa hora ({_fecha_legible(solicitud.fecha)} a las {solicitud.hora:%H:%M}) ya pasó. ¿Para qué otro día u hora la quieres?"

    # Evita duplicar si el cliente repite la misma reserva
    agenda_service.liberar_pendientes_vencidas(db, negocio.id_negocio)
    ya_existe = (
        db.query(Agenda)
        .filter(
            Agenda.id_cliente == id_cliente,
            Agenda.id_servicio == servicio.id_servicio,
            Agenda.fecha_cita == solicitud.fecha,
            Agenda.hora_cita == solicitud.hora,
            Agenda.estado_cita.in_(("pendiente", "confirmada")),
        )
        .first()
    )
    if ya_existe:
        return f"Ya tienes reservado *{servicio.nombre_servicio}* el {cuando} (estado: {ya_existe.estado_cita}). 👍"

    try:
        agenda_service.crear_cita_validada(
            db,
            id_negocio=negocio.id_negocio,
            id_cliente=id_cliente,
            servicio=servicio,
            fecha=solicitud.fecha,
            hora=solicitud.hora,
            horario_negocio=negocio.horario,
        )
    except FueraDeHorarioError:
        horario = f" Nuestro horario es: {negocio.horario}." if negocio.horario else ""
        return f"Esa hora queda fuera de nuestro horario de atención.{horario} ¿Te acomoda otro día u hora?"
    except HorarioOcupadoError:
        libres = _sugerir_horarios(db, negocio, servicio, solicitud.fecha, solicitud.hora, ahora)
        if libres:
            opciones = ", ".join(f"{h:%H:%M}" for h in libres)
            return (
                f"Uy, las {solicitud.hora:%H:%M} del {_fecha_legible(solicitud.fecha)} ya están tomadas. "
                f"Ese día tengo libre: {opciones}. ¿Te sirve alguna?"
            )
        return f"El {_fecha_legible(solicitud.fecha)} ya no me quedan horas libres después de las {solicitud.hora:%H:%M}. ¿Te acomoda otro día?"

    return (
        f"¡Listo! 🙌 Reservé *{servicio.nombre_servicio}* para el {cuando}.\n"
        f"Tu hora queda *pendiente* hasta que el equipo la confirme."
    )
