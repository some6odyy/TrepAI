"""
Agendado automático de citas desde el chat (interruptor "Agendar cita
automática" del bloque Contexto IA).

Cómo funciona:
1. Cuando el interruptor está encendido, el prompt le pide a la IA que, en
   cuanto el cliente haya dado servicio + fecha + hora, NO confirme la cita
   ella misma, sino que responda con un marcador:
       [[AGENDAR|Degradado|2026-11-12|16:00|Juan Pérez|juan@gmail.com|56912345678]]
   (los 3 últimos campos —nombre, correo y teléfono para recordatorios— son
   obligatorios: si faltan, el backend se los pide al cliente).
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
from app.models.cliente import Cliente
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import agenda_service
from app.services.agenda_service import (
    AnticipacionInsuficienteError,
    CitaActivaExistenteError,
    DatosContactoError,
    HorarioOcupadoError,
    SinCitaActivaError,
)
from app.services.horario_service import FueraDeHorarioError, validar_dentro_de_horario

_RE_CITA = re.compile(
    r"\[\[\s*(?P<accion>AGENDAR|REAGENDAR)\s*\|\s*(?P<servicio>[^|\]]+?)\s*\|\s*(?P<fecha>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<hora>\d{1,2}:\d{2})\s*"
    r"(?:\|\s*(?P<nombre>[^|\]]*?)\s*(?:\|\s*(?P<correo>[^|\]]*?)\s*(?:\|\s*(?P<telefono>[^|\]]*?)\s*)?)?)?\]\]",
    re.IGNORECASE,
)
_RE_REAGENDAR_SIN_SERVICIO = re.compile(
    r"\[\[\s*REAGENDAR\s*\|\s*(?P<fecha>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<hora>\d{1,2}:\d{2})\s*\]\]",
    re.IGNORECASE,
)
_RE_CANCELAR = re.compile(r"\[\[\s*CANCELAR\s*\]\]", re.IGNORECASE)

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
PASO_SUGERENCIAS_MIN = 30
MAX_SUGERENCIAS = 3


@dataclass
class SolicitudCita:
    accion: str  # "AGENDAR" | "REAGENDAR" | "CANCELAR"
    servicio: str | None = None
    fecha: date | None = None
    hora: time | None = None
    nombre: str | None = None
    correo: str | None = None
    telefono: str | None = None


def instrucciones_para_prompt() -> str:
    """Texto que se agrega al prompt cuando el agendado automático está activo."""
    return (
        "AGENDADO AUTOMÁTICO ACTIVADO: tú puedes reservar, reagendar y cancelar horas.\n"
        "- Interpreta fechas relativas (\"mañana\", \"el jueves\") con la fecha actual. "
        "Si el día de la semana y el número del día no coinciden, pregunta cuál es el correcto.\n"
        f"- Solo se agenda con al menos {agenda_service.ANTICIPACION_MINIMA_HORAS} horas de anticipación; "
        "no ofrezcas horas más cercanas que eso.\n"
        "- Para RESERVAR una hora nueva SIEMPRE necesitas: servicio, fecha, hora, nombre completo, "
        "correo electrónico y número de teléfono donde enviar los recordatorios y la confirmación. "
        "Pide lo que falte (puedes ofrecer usar el mismo número de WhatsApp desde el que escribe, "
        "pero que el cliente lo confirme). Si en 'Datos guardados del cliente' ya hay nombre o correo, "
        "pregúntale si quiere usar esos mismos.\n"
        "- NUEVA RESERVA (solo si el cliente NO tiene una cita activa): cuando tengas TODOS esos datos, "
        "responde ÚNICAMENTE con esta línea sin ningún otro texto:\n"
        "[[AGENDAR|<nombre exacto del servicio>|<AAAA-MM-DD>|<HH:MM>|<nombre completo>|<correo>|<teléfono con código de país>]]\n"
        "  Ejemplo: [[AGENDAR|Degradado|2026-11-14|16:00|Juan Pérez|juan.perez@gmail.com|56912345678]]\n"
        "- CAMBIAR O REAGENDAR HORA (si el cliente ya tiene una cita activa y pide moverla): "
        "NUNCA uses AGENDAR; responde ÚNICAMENTE con:\n"
        "[[REAGENDAR|<nombre exacto del servicio>|<AAAA-MM-DD>|<HH:MM>]]\n"
        "  Ejemplo: [[REAGENDAR|Degradado|2026-11-14|17:30]]\n"
        "- CANCELAR HORA (si el cliente pide anular su cita activa): responde ÚNICAMENTE con:\n"
        "[[CANCELAR]]\n"
        "- El sistema ejecuta la acción en la base de datos y responde al cliente; nunca digas tú "
        "que la hora quedó reservada, cambiada o cancelada ni inventes disponibilidad."
    )


def extraer_solicitud(texto: str) -> tuple[str, SolicitudCita | None]:
    """Separa los marcadores [[AGENDAR|...]], [[REAGENDAR|...]] o [[CANCELAR]] del texto de la IA."""
    raw = texto or ""

    if _RE_CANCELAR.search(raw):
        limpio = _RE_CANCELAR.sub("", raw).strip()
        return limpio, SolicitudCita(accion="CANCELAR")

    coincidencia = _RE_CITA.search(raw)
    if coincidencia:
        limpio = _RE_CITA.sub("", raw).strip()
        try:
            fecha = date.fromisoformat(coincidencia["fecha"])
            hh, mm = coincidencia["hora"].split(":")
            hora = time(int(hh), int(mm))
        except ValueError:
            return limpio, None
        return limpio, SolicitudCita(
            accion=coincidencia["accion"].upper(),
            servicio=coincidencia["servicio"].strip(),
            fecha=fecha,
            hora=hora,
            nombre=(coincidencia["nombre"] or "").strip() or None,
            correo=(coincidencia["correo"] or "").strip() or None,
            telefono=(coincidencia["telefono"] or "").strip() or None,
        )

    coincidencia_sin_serv = _RE_REAGENDAR_SIN_SERVICIO.search(raw)
    if coincidencia_sin_serv:
        limpio = _RE_REAGENDAR_SIN_SERVICIO.sub("", raw).strip()
        try:
            fecha = date.fromisoformat(coincidencia_sin_serv["fecha"])
            hh, mm = coincidencia_sin_serv["hora"].split(":")
            hora = time(int(hh), int(mm))
        except ValueError:
            return limpio, None
        return limpio, SolicitudCita(accion="REAGENDAR", fecha=fecha, hora=hora)

    return raw, None


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def buscar_servicio(nombre: str, servicios: list[Servicio]) -> Servicio | None:
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


def _sugerir_horarios(
    db: Session,
    negocio: Negocio,
    servicio: Servicio,
    fecha: date,
    desde: time,
    ahora: datetime,
    excluir_id_agenda: int | None = None,
) -> list[time]:
    duracion = agenda_service.duracion_minutos(servicio)
    sugerencias: list[time] = []
    actual = datetime.combine(fecha, desde)
    fin_del_dia = datetime.combine(fecha, time(23, 59))
    minimo = ahora + timedelta(hours=agenda_service.ANTICIPACION_MINIMA_HORAS)
    while actual <= fin_del_dia and len(sugerencias) < MAX_SUGERENCIAS:
        if actual >= minimo:
            try:
                validar_dentro_de_horario(negocio.horario, fecha, actual.time(), duracion)
                agenda_service.validar_horario_libre(
                    db,
                    negocio.id_negocio,
                    fecha,
                    actual.time(),
                    duracion,
                    excluir_id_agenda=excluir_id_agenda,
                )
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
    """Ejecuta AGENDAR, REAGENDAR o CANCELAR y devuelve el mensaje final al cliente."""
    if solicitud.accion == "CANCELAR":
        try:
            cita_cancelada = agenda_service.cancelar_cita_activa(db, negocio.id_negocio, id_cliente)
        except SinCitaActivaError:
            return "No tienes ninguna cita activa para cancelar. Si quieres agendar una hora nueva, dime qué servicio, día y hora prefieres."

        servicio = db.query(Servicio).filter(Servicio.id_servicio == cita_cancelada.id_servicio).first()
        nombre_serv = servicio.nombre_servicio if servicio else "tu servicio"
        cuando = f"*{_fecha_legible(cita_cancelada.fecha_cita)}* a las *{cita_cancelada.hora_cita:%H:%M}*"
        return f"Listo, cancelé tu cita de *{nombre_serv}* del {cuando}. Cuando quieras volver a agendar, escríbeme."

    servicios = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()
    ahora = ahora_local.replace(tzinfo=None)
    assert solicitud.fecha is not None and solicitud.hora is not None
    inicio = datetime.combine(solicitud.fecha, solicitud.hora)
    cuando = f"*{_fecha_legible(solicitud.fecha)}* a las *{solicitud.hora:%H:%M}*"

    if inicio <= ahora:
        return f"Esa hora ({_fecha_legible(solicitud.fecha)} a las {solicitud.hora:%H:%M}) ya pasó. ¿Para qué otro día u hora la quieres?"

    minimo = ahora + timedelta(hours=agenda_service.ANTICIPACION_MINIMA_HORAS)
    if inicio < minimo:
        return _mensaje_anticipacion(db, negocio, servicios, solicitud, ahora, minimo)

    if solicitud.accion == "REAGENDAR":
        existente = agenda_service.obtener_cita_activa_cliente(db, negocio.id_negocio, id_cliente)
        if existente is None:
            return "No encontré una cita activa para cambiar. Dime qué servicio, día y hora quieres reservar."

        cita_actual, servicio_actual = existente
        servicio_nuevo = (
            buscar_servicio(solicitud.servicio, servicios)
            if solicitud.servicio
            else servicio_actual
        ) or servicio_actual

        try:
            agenda_service.reagendar_cita_validada(
                db,
                id_negocio=negocio.id_negocio,
                id_cliente=id_cliente,
                nueva_fecha=solicitud.fecha,
                nueva_hora=solicitud.hora,
                nuevo_servicio=servicio_nuevo,
                horario_negocio=negocio.horario,
                ahora_local=ahora,
            )
        except AnticipacionInsuficienteError as err:
            return _mensaje_anticipacion(db, negocio, servicios, solicitud, ahora, err.minimo)
        except FueraDeHorarioError:
            horario = f" Nuestro horario es: {negocio.horario}." if negocio.horario else ""
            return f"Esa hora queda fuera de nuestro horario de atención.{horario} ¿A qué otra hora la cambiamos?"
        except HorarioOcupadoError:
            libres = _sugerir_horarios(
                db,
                negocio,
                servicio_nuevo,
                solicitud.fecha,
                solicitud.hora,
                ahora,
                excluir_id_agenda=cita_actual.id_agenda,
            )
            if libres:
                opciones = ", ".join(f"{h:%H:%M}" for h in libres)
                return (
                    f"Uy, las {solicitud.hora:%H:%M} del {_fecha_legible(solicitud.fecha)} ya están tomadas. "
                    f"Ese día tengo libre: {opciones}. ¿Te sirve alguna para el cambio?"
                )
            return f"El {_fecha_legible(solicitud.fecha)} ya no me quedan horas libres después de las {solicitud.hora:%H:%M}. ¿Te acomoda otro día?"

        return (
            f"¡Listo! 🙌 Cambié tu hora de *{servicio_nuevo.nombre_servicio}* para el {cuando}.\n"
            f"La cita anterior quedó liberada y esta nueva hora queda *pendiente* hasta que el equipo la confirme. "
            f"Te llegarán los recordatorios con la nueva hora."
        )

    # Acción: AGENDAR (nueva cita)
    servicio = buscar_servicio(solicitud.servicio or "", servicios)
    if servicio is None:
        nombres = ", ".join(s.nombre_servicio for s in servicios) or "ninguno cargado"
        return f"No encontré el servicio \"{solicitud.servicio}\". ¿Cuál de estos quieres? {nombres}"

    try:
        agenda_service.crear_cita_validada(
            db,
            id_negocio=negocio.id_negocio,
            id_cliente=id_cliente,
            servicio=servicio,
            fecha=solicitud.fecha,
            hora=solicitud.hora,
            horario_negocio=negocio.horario,
            permitir_duplicado_cliente=False,
            nombre_contacto=solicitud.nombre,
            correo_contacto=solicitud.correo,
            telefono_contacto=solicitud.telefono,
            ahora_local=ahora,
        )
    except DatosContactoError as err:
        return _mensaje_datos_contacto(err)
    except AnticipacionInsuficienteError as err:
        return _mensaje_anticipacion(db, negocio, servicios, solicitud, ahora, err.minimo)
    except CitaActivaExistenteError as err:
        actual_txt = f"*{_fecha_legible(err.cita.fecha_cita)}* a las *{err.cita.hora_cita:%H:%M}*"
        mismo_horario = (
            err.cita.fecha_cita == solicitud.fecha
            and err.cita.hora_cita.replace(second=0, microsecond=0) == solicitud.hora.replace(second=0, microsecond=0)
            and err.servicio.id_servicio == servicio.id_servicio
        )
        if mismo_horario:
            # Pidió exactamente la cita que ya tiene: no hay nada que mover.
            return (
                f"Ya tienes reservado *{err.servicio.nombre_servicio}* el {actual_txt} "
                f"({err.cita.estado_cita}). No hace falta agendar de nuevo 🙌"
            )
        return (
            f"Ya tienes una cita activa de *{err.servicio.nombre_servicio}* el {actual_txt} ({err.cita.estado_cita}). "
            f"Si quieres cambiarla para el {cuando}, confírmamelo y la muevo sin duplicarla."
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

    # Guardamos nombre/correo en la ficha del cliente para la próxima reserva
    cliente = db.query(Cliente).filter(Cliente.id_cliente == id_cliente).first()
    if cliente is not None:
        if not cliente.nombre_cliente and solicitud.nombre:
            cliente.nombre_cliente = solicitud.nombre.strip()[:100]
        correo_ok = agenda_service.normalizar_correo(solicitud.correo)
        if correo_ok:
            cliente.correo = correo_ok
        db.commit()

    return (
        f"¡Listo! 🙌 Reservé *{servicio.nombre_servicio}* para el {cuando}.\n"
        f"Tu hora queda *pendiente* hasta que el equipo la confirme. "
        f"Te enviaremos un recordatorio el día antes y te pediremos confirmar 1 hora antes."
    )


_NOMBRES_CAMPO = {"nombre": "tu nombre completo", "correo": "tu correo electrónico", "teléfono": "un número de teléfono"}


def _mensaje_datos_contacto(err: DatosContactoError) -> str:
    partes = [_NOMBRES_CAMPO.get(c, c) for c in err.faltantes]
    texto = ""
    if partes:
        lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
        texto = f"Para dejar tu hora reservada necesito {lista} para enviarte los recordatorios y la confirmación."
    if err.invalidos:
        malos = " y ".join("el correo" if c == "correo" else "el teléfono" for c in err.invalidos)
        texto += (" " if texto else "") + f"Revisa {malos}, parece que no está bien escrito."
    return texto + " ¿Me lo compartes?"


def _mensaje_anticipacion(
    db: Session,
    negocio: Negocio,
    servicios: list[Servicio],
    solicitud: SolicitudCita,
    ahora: datetime,
    minimo: datetime,
) -> str:
    horas = agenda_service.ANTICIPACION_MINIMA_HORAS
    base = f"Las horas se agendan con al menos *{horas} horas de anticipación*."
    servicio = buscar_servicio(solicitud.servicio or "", servicios) if solicitud.servicio else None
    if servicio is not None:
        desde = minimo.time().replace(second=0, microsecond=0)
        # redondea hacia arriba al siguiente bloque de 30 min
        extra = (-desde.minute) % PASO_SUGERENCIAS_MIN
        inicio = datetime.combine(minimo.date(), desde) + timedelta(minutes=extra)
        libres = _sugerir_horarios(db, negocio, servicio, inicio.date(), inicio.time(), ahora)
        if libres:
            opciones = ", ".join(f"{h:%H:%M}" for h in libres)
            return f"{base} Para el {_fecha_legible(inicio.date())} tengo libre: {opciones}. ¿Te sirve alguna?"
    return f"{base} Lo más pronto es desde las {minimo:%H:%M} del {_fecha_legible(minimo.date())}. ¿Qué otra hora te acomoda?"
