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

from app.core.tiempo import ahora_chile_naive
from app.models.agenda import Agenda
from app.models.cliente import Cliente
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import agenda_service
from app.services.agenda_service import (
    AnticipacionInsuficienteError,
    ChoqueConCitaPropiaError,
    CitaActivaExistenteError,
    CitaAmbiguaError,
    DatosContactoError,
    HorarioBloqueadoError,
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
    r"\[\[\s*REAGENDAR\s*\|\s*(?P<fecha>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<hora>\d{1,2}:\d{2})\s*"
    r"(?:\|\s*#?(?P<id>\d+)\s*)?\]\]",
    re.IGNORECASE,
)
# [[CANCELAR]] o [[CANCELAR|<id de la cita>]] (cuando el cliente tiene varias)
_RE_CANCELAR = re.compile(r"\[\[\s*CANCELAR\s*(?:\|\s*#?(?P<id>\d+)\s*)?\]\]", re.IGNORECASE)
_RE_ID = re.compile(r"^#?\s*(\d+)$")
# Cambiar los datos de contacto de la cita activa: [[CONTACTO|nombre|correo|teléfono]]
# ("-" o vacío = ese dato no cambia)
_RE_CONTACTO = re.compile(
    r"\[\[\s*CONTACTO\s*\|\s*(?P<nombre>[^|\]]*?)\s*\|\s*(?P<correo>[^|\]]*?)\s*\|\s*(?P<telefono>[^|\]]*?)\s*\]\]",
    re.IGNORECASE,
)

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
PASO_SUGERENCIAS_MIN = 30
MAX_SUGERENCIAS = 3


@dataclass
class SolicitudCita:
    accion: str  # "AGENDAR" | "REAGENDAR" | "CANCELAR" | "CONTACTO"
    servicio: str | None = None
    fecha: date | None = None
    hora: time | None = None
    nombre: str | None = None
    correo: str | None = None
    telefono: str | None = None
    # Cita a cambiar/cancelar cuando el cliente tiene varias (id_agenda)
    id_cita: int | None = None
    # True si nombre/correo/teléfono se tomaron de otra cita activa del cliente
    contacto_reutilizado: bool = False


def instruccion_contacto() -> str:
    """Cómo pedir el cambio de teléfono/correo/nombre de la cita activa. Va en
    el prompt aunque el agendado automático esté apagado (la cita pudo
    crearse a mano en el Dashboard)."""
    return (
        "- CAMBIAR DATOS DE CONTACTO (si el cliente ya tiene una cita activa y quiere que los recordatorios "
        "lleguen a otro teléfono o correo, o corregir su nombre; ej. \"mi teléfono es +56 9 8140 7529\", "
        "\"mejor mándalo a este número\", \"cambia mi correo\"): responde ÚNICAMENTE con:\n"
        "[[CONTACTO|<nombre o ->|<correo o ->|<teléfono con código de país o ->]]\n"
        "  Usa - en los datos que NO cambian. Ejemplo: [[CONTACTO|-|-|56981407529]]\n"
        "  Si dice \"este número\" o \"este mismo\", usa el WhatsApp desde el que escribe.\n"
    )


def instrucciones_para_prompt() -> str:
    """Texto que se agrega al prompt cuando el agendado automático está activo."""
    return (
        "AGENDADO AUTOMÁTICO ACTIVADO: tú puedes reservar, reagendar y cancelar horas.\n"
        "- Interpreta fechas relativas (\"mañana\", \"el jueves\") con la fecha actual. "
        "Si el día de la semana y el número del día no coinciden, pregunta cuál es el correcto.\n"
        f"- Solo se agenda con al menos {agenda_service.ANTICIPACION_MINIMA_HORAS} horas de anticipación; "
        "no ofrezcas horas más cercanas que eso.\n"
        "- UN CLIENTE PUEDE TENER VARIAS CITAS ACTIVAS a la vez (ej. un corte el viernes y la barba el sábado), "
        "siempre que no se crucen entre sí ni con otras reservas. Revisa 'Citas activas del cliente': "
        "cada una tiene un número (#id).\n"
        "- Para RESERVAR una hora nueva SIEMPRE necesitas: servicio, fecha, hora, nombre completo, "
        "correo electrónico y número de teléfono donde enviar los recordatorios y la confirmación. "
        "Pide lo que falte (puedes ofrecer usar el mismo número de WhatsApp desde el que escribe, "
        "pero que el cliente lo confirme). Si en 'Datos guardados del cliente' o en sus citas activas ya hay "
        "nombre, correo o teléfono, pregúntale si quiere usar esos mismos.\n"
        "- NUEVA RESERVA (también si ya tiene otras citas y pide una hora ADICIONAL): cuando tengas TODOS esos datos, "
        "responde ÚNICAMENTE con esta línea sin ningún otro texto:\n"
        "[[AGENDAR|<nombre exacto del servicio>|<AAAA-MM-DD>|<HH:MM>|<nombre completo>|<correo>|<teléfono con código de país>]]\n"
        "  Ejemplo: [[AGENDAR|Degradado|2026-11-14|16:00|Juan Pérez|juan.perez@gmail.com|56912345678]]\n"
        "- CAMBIAR O REAGENDAR UNA HORA QUE YA TIENE (pide mover una cita existente, no una adicional): "
        "NUNCA uses AGENDAR; responde ÚNICAMENTE con:\n"
        "[[REAGENDAR|<nombre exacto del servicio>|<AAAA-MM-DD nueva>|<HH:MM nueva>|<#id de la cita que se mueve>]]\n"
        "  Ejemplo: [[REAGENDAR|Degradado|2026-11-14|17:30|#12]]\n"
        "  Si tiene varias citas y no queda claro cuál quiere mover, pregúntale cuál antes de usar el marcador.\n"
        "  Si no está claro si quiere MOVER una cita o agendar OTRA adicional, pregúntale.\n"
        "- CANCELAR HORA: responde ÚNICAMENTE con [[CANCELAR|<#id de la cita>]] (ej. [[CANCELAR|#12]]). "
        "Si tiene varias y no dice cuál, pregúntale primero.\n"
        f"{instruccion_contacto()}"
        "- El sistema ejecuta la acción en la base de datos y responde al cliente; nunca digas tú "
        "que la hora quedó reservada, cambiada o cancelada ni inventes disponibilidad."
    )


def _dato_marcador(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return None if valor in ("", "-", "—", "–") else valor


def extraer_solicitud(texto: str) -> tuple[str, SolicitudCita | None]:
    """Separa los marcadores [[AGENDAR|...]], [[REAGENDAR|...]], [[CANCELAR]]
    o [[CONTACTO|...]] del texto de la IA."""
    raw = texto or ""

    contacto = _RE_CONTACTO.search(raw)
    if contacto:
        limpio = _RE_CONTACTO.sub("", raw).strip()
        return limpio, SolicitudCita(
            accion="CONTACTO",
            nombre=_dato_marcador(contacto["nombre"]),
            correo=_dato_marcador(contacto["correo"]),
            telefono=_dato_marcador(contacto["telefono"]),
        )

    cancelar = _RE_CANCELAR.search(raw)
    if cancelar:
        limpio = _RE_CANCELAR.sub("", raw).strip()
        return limpio, SolicitudCita(accion="CANCELAR", id_cita=int(cancelar["id"]) if cancelar["id"] else None)

    coincidencia = _RE_CITA.search(raw)
    if coincidencia:
        limpio = _RE_CITA.sub("", raw).strip()
        try:
            fecha = date.fromisoformat(coincidencia["fecha"])
            hh, mm = coincidencia["hora"].split(":")
            hora = time(int(hh), int(mm))
        except ValueError:
            return limpio, None
        accion = coincidencia["accion"].upper()
        nombre = (coincidencia["nombre"] or "").strip() or None
        if accion == "REAGENDAR":
            # [[REAGENDAR|servicio|fecha|hora|#id]]: el 5° campo es la cita a mover
            id_txt = _RE_ID.match(nombre or "")
            return limpio, SolicitudCita(
                accion=accion, servicio=coincidencia["servicio"].strip(), fecha=fecha, hora=hora,
                id_cita=int(id_txt.group(1)) if id_txt else None,
            )
        return limpio, SolicitudCita(
            accion=accion,
            servicio=coincidencia["servicio"].strip(),
            fecha=fecha,
            hora=hora,
            nombre=nombre,
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
        return limpio, SolicitudCita(
            accion="REAGENDAR", fecha=fecha, hora=hora,
            id_cita=int(coincidencia_sin_serv["id"]) if coincidencia_sin_serv["id"] else None,
        )

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
    """"sábado 28-11" (con el año si no es el actual: "lunes 29-11-2027")."""
    anio = "" if fecha.year == ahora_chile_naive().year else f"-{fecha.year}"
    return f"{_DIAS[fecha.weekday()]} {fecha:%d-%m}{anio}"


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


def describir_cita(cita: Agenda, servicio: Servicio) -> str:
    return f"*{servicio.nombre_servicio}* el {_fecha_legible(cita.fecha_cita)} a las {cita.hora_cita:%H:%M}"


def _mensaje_cual_cita(citas: list, accion: str) -> str:
    """El cliente tiene varias citas y no se sabe cuál cambiar/cancelar."""
    lineas = "\n".join(f"{i}. {describir_cita(c, s)}" for i, (c, s) in enumerate(citas, 1))
    ejemplo = (
        f"cancela el {citas[0][1].nombre_servicio}" if accion == "cancelar"
        else f"cambia el {citas[0][1].nombre_servicio} al sábado a las 15"
    )
    return (
        f"Tienes {len(citas)} horas reservadas:\n{lineas}\n"
        f"¿Cuál quieres {accion}? Dime el servicio o el número (ej. \"{ejemplo}\")."
    )


def _mensaje_choque_propio(err: ChoqueConCitaPropiaError, solicitud) -> str:
    return (
        f"Esa hora choca con tu otra cita de {describir_cita(err.cita, err.servicio)} 🙈 "
        f"Tus horas no se pueden cruzar entre sí. ¿Te acomoda otra hora?"
    )


def _completar_contacto_desde_citas(db: Session, negocio: Negocio, id_cliente: int, solicitud: SolicitudCita) -> None:
    """Si pide una hora ADICIONAL y no repitió nombre/correo/teléfono, se usan
    los de su cita activa más próxima (ya los dio al reservar antes)."""
    if solicitud.nombre and solicitud.correo and solicitud.telefono:
        return
    activas = agenda_service.citas_activas_cliente(db, negocio.id_negocio, id_cliente)
    if not activas:
        return
    previa = activas[0][0]
    solicitud.contacto_reutilizado = True
    solicitud.nombre = solicitud.nombre or previa.nombre_contacto
    solicitud.correo = solicitud.correo or previa.correo_contacto
    solicitud.telefono = solicitud.telefono or previa.telefono_contacto


def procesar_solicitud(
    db: Session,
    negocio: Negocio,
    id_cliente: int,
    solicitud: SolicitudCita,
    ahora_local: datetime,
) -> str:
    """Ejecuta AGENDAR, REAGENDAR, CANCELAR o CONTACTO y devuelve el mensaje final al cliente."""
    if solicitud.accion == "CONTACTO":
        return actualizar_contacto(db, negocio, id_cliente, solicitud.nombre, solicitud.correo, solicitud.telefono)

    if solicitud.accion == "CANCELAR":
        servicios_neg = db.query(Servicio).filter(Servicio.id_negocio == negocio.id_negocio).all()
        servicio_nombrado = buscar_servicio(solicitud.servicio, servicios_neg) if solicitud.servicio else None
        try:
            cita_cancelada = agenda_service.cancelar_cita_activa(
                db, negocio.id_negocio, id_cliente, id_agenda=solicitud.id_cita, servicio=servicio_nombrado
            )
        except SinCitaActivaError:
            return "No tienes ninguna cita activa para cancelar. Si quieres agendar una hora nueva, dime qué servicio, día y hora prefieres."
        except CitaAmbiguaError as err:
            return _mensaje_cual_cita(err.citas, "cancelar")

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
        servicio_pedido = buscar_servicio(solicitud.servicio, servicios) if solicitud.servicio else None
        try:
            cita_actual, servicio_actual = agenda_service.elegir_cita_cliente(
                db, negocio.id_negocio, id_cliente, id_agenda=solicitud.id_cita, servicio=servicio_pedido
            )
        except SinCitaActivaError:
            return "No encontré una cita activa para cambiar. Dime qué servicio, día y hora quieres reservar."
        except CitaAmbiguaError as err:
            return _mensaje_cual_cita(err.citas, "cambiar")

        servicio_nuevo = servicio_pedido or servicio_actual
        antes = describir_cita(cita_actual, servicio_actual)

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
                id_agenda=cita_actual.id_agenda,
            )
        except AnticipacionInsuficienteError as err:
            return _mensaje_anticipacion(db, negocio, servicios, solicitud, ahora, err.minimo)
        except FueraDeHorarioError:
            horario = f" Nuestro horario es: {negocio.horario}." if negocio.horario else ""
            return f"Esa hora queda fuera de nuestro horario de atención.{horario} ¿A qué otra hora la cambiamos?"
        except ChoqueConCitaPropiaError as err:
            return _mensaje_choque_propio(err, solicitud)
        except HorarioBloqueadoError as err:
            return _mensaje_bloqueo(db, negocio, servicio_nuevo, solicitud, ahora, err, cita_actual.id_agenda)
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

        otras = len(agenda_service.citas_activas_cliente(db, negocio.id_negocio, id_cliente)) - 1
        nota_otras = (
            "\nTu otra hora reservada sigue igual." if otras == 1
            else f"\nTus otras {otras} horas reservadas siguen igual." if otras > 1 else ""
        )
        return (
            f"¡Listo! 🙌 Cambié tu hora de {antes} para el {cuando}"
            f"{'' if servicio_nuevo.id_servicio == servicio_actual.id_servicio else f' (ahora *{servicio_nuevo.nombre_servicio}*)'}.\n"
            f"La hora anterior quedó liberada y esta nueva queda *pendiente* hasta que el equipo la confirme. "
            f"Te llegarán los recordatorios con la nueva hora.{nota_otras}"
        )

    # Acción: AGENDAR (nueva cita)
    servicio = buscar_servicio(solicitud.servicio or "", servicios)
    if servicio is None:
        nombres = ", ".join(s.nombre_servicio for s in servicios) or "ninguno cargado"
        return f"No encontré el servicio \"{solicitud.servicio}\". ¿Cuál de estos quieres? {nombres}"

    _completar_contacto_desde_citas(db, negocio, id_cliente, solicitud)
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
        # Pidió exactamente una cita que ya tiene: no se duplica.
        return (
            f"Ya tienes reservado {describir_cita(err.cita, err.servicio)} ({err.cita.estado_cita}). "
            f"No hace falta agendar de nuevo 🙌"
        )
    except ChoqueConCitaPropiaError as err:
        return _mensaje_choque_propio(err, solicitud)
    except FueraDeHorarioError:
        horario = f" Nuestro horario es: {negocio.horario}." if negocio.horario else ""
        return f"Esa hora queda fuera de nuestro horario de atención.{horario} ¿Te acomoda otro día u hora?"
    except HorarioBloqueadoError as err:
        return _mensaje_bloqueo(db, negocio, servicio, solicitud, ahora, err)
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
        if solicitud.nombre and solicitud.nombre.strip():
            # El nombre que da al agendar manda sobre el de su perfil de WhatsApp
            cliente.nombre_cliente = " ".join(solicitud.nombre.split())[:100]
        correo_ok = agenda_service.normalizar_correo(solicitud.correo)
        if correo_ok:
            cliente.correo = correo_ok
        db.commit()

    total = len(agenda_service.citas_activas_cliente(db, negocio.id_negocio, id_cliente))
    nota_total = f"\nCon esta tienes {total} horas reservadas con nosotros." if total > 1 else ""
    if solicitud.contacto_reutilizado:
        nota_total += (
            f"\nUsé los mismos datos de tu otra hora ({solicitud.nombre}, {solicitud.correo}); "
            f"si quieres otros para esta, dímelo."
        )
    return (
        f"¡Listo! 🙌 Reservé *{servicio.nombre_servicio}* para el {cuando}.\n"
        f"Tu hora queda *pendiente* hasta que el equipo la confirme. "
        f"Te enviaremos un recordatorio el día antes y te pediremos confirmar 1 hora antes.{nota_total}"
    )


def _mensaje_bloqueo(db, negocio, servicio, solicitud, ahora, err, excluir_id_agenda=None) -> str:
    """El negocio bloqueó ese día u horario (vacaciones, feriado, almuerzo)."""
    motivo = f" ({err.motivo})" if err.motivo else ""
    if err.dia_completo:
        return f"El {_fecha_legible(solicitud.fecha)} no atendemos{motivo} 🙏 ¿Te acomoda otro día?"
    libres = _sugerir_horarios(
        db, negocio, servicio, solicitud.fecha, solicitud.hora, ahora, excluir_id_agenda=excluir_id_agenda
    ) or _sugerir_horarios(db, negocio, servicio, solicitud.fecha, time(0, 0), ahora, excluir_id_agenda=excluir_id_agenda)
    base = f"A esa hora no atendemos{motivo}."
    if libres:
        return f"{base} El {_fecha_legible(solicitud.fecha)} tengo libre: {', '.join(f'{h:%H:%M}' for h in libres)}. ¿Te sirve alguna?"
    return f"{base} ¿Te acomoda otro día u hora?"


def _telefono_legible(digitos: str) -> str:
    """56981407529 -> +56 9 8140 7529 (otros formatos: +<dígitos>)."""
    if len(digitos) == 11 and digitos.startswith("569"):
        return f"+56 9 {digitos[3:7]} {digitos[7:]}"
    return f"+{digitos}"


def actualizar_contacto(
    db: Session,
    negocio: Negocio,
    id_cliente: int,
    nombre: str | None = None,
    correo: str | None = None,
    telefono: str | None = None,
) -> str:
    """Cambia los datos de contacto de la cita activa (a los que llegan los
    recordatorios y la confirmación), avisa al negocio y devuelve el mensaje
    para el cliente. Lo usan el agendado con IA ([[CONTACTO|...]]) y el
    modo sin IA."""
    from app.services import recordatorios  # evita import circular

    if not any((nombre, correo, telefono)):
        return "¿Qué dato quieres cambiar? Puedes enviarme el nuevo *teléfono*, *correo* o *nombre* para los recordatorios."
    try:
        cita, servicio, cambios = agenda_service.actualizar_contacto_cita_activa(
            db, negocio.id_negocio, id_cliente, nombre=nombre, correo=correo, telefono=telefono
        )
    except SinCitaActivaError:
        return (
            "No tienes una hora agendada en este momento, así que no hay recordatorios que cambiar. "
            "Si quieres reservar, dime qué servicio, día y hora prefieres."
        )
    except DatosContactoError as err:
        malos = " y ".join(
            {"correo": "el *correo*", "teléfono": "el *teléfono*", "nombre": "el *nombre*"}[c] for c in err.invalidos
        )
        ejemplo = " (ej. +56 9 1234 5678)" if "teléfono" in err.invalidos else ""
        return f"Revisa {malos}{ejemplo}, parece que no está bien escrito. ¿Me lo envías de nuevo?"

    cuando = f"*{_fecha_legible(cita.fecha_cita)}* a las *{cita.hora_cita:%H:%M}*"
    if not cambios:
        return (
            f"Esos datos ya son los de tu hora de *{servicio.nombre_servicio}* del {cuando}, "
            f"no hay nada que cambiar 🙌"
        )

    legibles = []
    if "teléfono" in cambios:
        legibles.append(f"teléfono: *{_telefono_legible(cambios['teléfono'])}*")
    if "correo" in cambios:
        legibles.append(f"correo: *{cambios['correo']}*")
    if "nombre" in cambios:
        legibles.append(f"nombre: *{cambios['nombre']}*")

    cliente = db.query(Cliente).filter(Cliente.id_cliente == id_cliente).first()
    quien = cita.nombre_contacto or (cliente.nombre_cliente if cliente else None) or "Un cliente"
    recordatorios.crear_aviso(
        db,
        negocio.id_negocio,
        "contacto_actualizado",
        f"✏️ {quien} actualizó los datos de su cita de {servicio.nombre_servicio} "
        f"({_fecha_legible(cita.fecha_cita)} {cita.hora_cita:%H:%M}): " + ", ".join(
            texto.replace("*", "") for texto in legibles
        ),
        cita.id_agenda,
    )

    nota = ""
    whatsapp_cliente = cliente.telefono if cliente else None
    if "teléfono" in cambios and cambios["teléfono"] != whatsapp_cliente:
        nota = (
            "\nSi ese número todavía no nos ha escrito por WhatsApp, puede que los recordatorios "
            "te lleguen solo por correo, así que mándanos un \"hola\" desde él."
        )
    return (
        f"¡Listo! ✅ Actualicé los datos de tu hora de *{servicio.nombre_servicio}* del {cuando}: "
        f"{', '.join(legibles)}.\nLos recordatorios y la confirmación te llegarán ahí.{nota}"
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
