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

from app.models.cliente import Cliente
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


class HorarioBloqueadoError(HorarioOcupadoError):
    """El intervalo cae en un horario bloqueado por el negocio (vacaciones,
    feriado, almuerzo). Hereda de HorarioOcupadoError para que todo el código
    que ya maneja "horario ocupado" (sugerencias de horas libres, 409 en la
    API) lo trate igual."""

    def __init__(self, motivo: str | None, inicio: time, fin: time, dia_completo: bool = False):
        self.motivo = motivo
        self.dia_completo = dia_completo
        self.conflicto_inicio = inicio
        self.conflicto_fin = fin
        detalle = f" ({motivo})" if motivo else ""
        if dia_completo:
            texto = f"Ese día el negocio no atiende{detalle}."
        else:
            texto = f"Ese horario está bloqueado de {inicio:%H:%M} a {fin:%H:%M}{detalle}."
        Exception.__init__(self, texto)


class CitaActivaExistenteError(Exception):
    """El cliente ya tiene reservada exactamente esa cita (mismo servicio,
    día y hora). Un cliente SÍ puede tener varias citas activas a la vez."""

    def __init__(self, cita: Agenda, servicio: Servicio):
        self.cita = cita
        self.servicio = servicio
        super().__init__(
            f"Ya tienes una cita activa de {servicio.nombre_servicio} el "
            f"{cita.fecha_cita:%d-%m} a las {cita.hora_cita:%H:%M}."
        )


class SinCitaActivaError(Exception):
    """El cliente intentó reagendar o cancelar sin tener una cita activa."""


class CitaAmbiguaError(Exception):
    """El cliente tiene varias citas activas y no se sabe cuál quiere cambiar
    o cancelar. `citas` trae la lista [(Agenda, Servicio), ...]."""

    def __init__(self, citas: list):
        self.citas = citas
        super().__init__("Tienes varias citas activas: indica cuál.")


class ChoqueConCitaPropiaError(HorarioOcupadoError):
    """El horario pedido se solapa con OTRA cita activa del mismo cliente
    (puede tener varias, pero no a la misma hora)."""

    def __init__(self, cita: Agenda, servicio: Servicio, inicio: time, fin: time):
        self.cita = cita
        self.servicio = servicio
        self.conflicto_inicio = inicio
        self.conflicto_fin = fin
        Exception.__init__(
            self,
            f"Ese horario choca con tu otra cita de {servicio.nombre_servicio} "
            f"({cita.fecha_cita:%d-%m} de {inicio:%H:%M} a {fin:%H:%M}).",
        )


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


def citas_activas_cliente(
    db: Session, id_negocio: int, id_cliente: int, ahora_local: datetime | None = None
) -> list[tuple[Agenda, Servicio]]:
    """Todas las citas 'pendiente' o 'confirmada' del cliente que todavía no
    terminan, de la más próxima a la más lejana. Un cliente puede tener
    varias (ej. corte el viernes y barba el sábado)."""
    liberar_pendientes_vencidas(db, id_negocio)
    if ahora_local is None:
        from app.services import ai_service  # reloj del bot (las pruebas lo fijan)
        ahora_local = ai_service.ahora_chile()
    ahora = ahora_local.replace(tzinfo=None)
    filas = (
        db.query(Agenda, Servicio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .filter(
            Servicio.id_negocio == id_negocio,
            Agenda.id_cliente == id_cliente,
            Agenda.estado_cita.in_(("pendiente", "confirmada")),
            Agenda.fecha_cita >= ahora.date(),
        )
        .order_by(Agenda.fecha_cita.asc(), Agenda.hora_cita.asc(), Agenda.id_agenda.asc())
        .all()
    )
    # Las de hoy que ya terminaron no cuentan como "activas"
    return [
        (cita, servicio) for cita, servicio in filas
        if inicio_cita(cita) + timedelta(minutes=duracion_minutos(servicio)) > ahora
    ]


def obtener_cita_activa_cliente(
    db: Session, id_negocio: int, id_cliente: int
) -> tuple[Agenda, Servicio] | None:
    """Devuelve la cita 'pendiente' o 'confirmada' más próxima del cliente."""
    activas = citas_activas_cliente(db, id_negocio, id_cliente)
    return activas[0] if activas else None


def elegir_cita_cliente(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    id_agenda: int | None = None,
    servicio: Servicio | None = None,
) -> tuple[Agenda, Servicio]:
    """Decide QUÉ cita activa quiere cambiar o cancelar el cliente:
    1) la indicada por id (si es suya y está activa),
    2) si tiene una sola, esa,
    3) si nombró un servicio y solo una de sus citas es de ese servicio, esa.
    Si no se puede decidir lanza CitaAmbiguaError; si no tiene, SinCitaActivaError."""
    activas = citas_activas_cliente(db, id_negocio, id_cliente)
    if not activas:
        raise SinCitaActivaError("No tienes una cita activa.")
    if id_agenda is not None:
        for cita, serv in activas:
            if cita.id_agenda == id_agenda:
                return cita, serv
    if len(activas) == 1:
        return activas[0]
    if servicio is not None:
        del_servicio = [(c, s) for c, s in activas if s.id_servicio == servicio.id_servicio]
        if len(del_servicio) == 1:
            return del_servicio[0]
    raise CitaAmbiguaError(activas)


def validar_sin_choque_propio(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    fecha: date,
    hora: time,
    duracion_min: int,
    excluir_id_agenda: int | None = None,
) -> None:
    """El cliente puede tener varias citas, pero no dos que se solapen."""
    nuevo_ini, nuevo_fin = _intervalo(fecha, hora, duracion_min)
    for cita, servicio in citas_activas_cliente(db, id_negocio, id_cliente):
        if cita.id_agenda == excluir_id_agenda or cita.fecha_cita != fecha:
            continue
        ini, fin = _intervalo(cita.fecha_cita, cita.hora_cita, duracion_minutos(servicio))
        if nuevo_ini < fin and ini < nuevo_fin:
            raise ChoqueConCitaPropiaError(cita, servicio, ini.time(), fin.time())


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

    # Horarios bloqueados por el negocio (vacaciones, feriados, almuerzo)
    from app.services import bloqueo_service  # evita import circular
    for ini, fin, bloqueo in bloqueo_service.tramos_del_dia(db, id_negocio, fecha):
        if nuevo_ini < fin and ini < nuevo_fin:
            dia_completo = ini.time() == time(0, 0) and fin - ini >= timedelta(hours=24)
            raise HorarioBloqueadoError(bloqueo.motivo, ini.time(), (fin - timedelta(minutes=1)).time() if dia_completo else fin.time(), dia_completo)


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
    """Crea una cita 'pendiente'. Un cliente puede tener VARIAS citas activas;
    desde el flujo IA se pasa permitir_duplicado_cliente=False para no crear
    dos veces exactamente la misma cita (mismo servicio, día y hora) si el
    cliente repite el mensaje. Tampoco se permite que se solape con otra
    cita del mismo cliente (ChoqueConCitaPropiaError).

    Exige nombre, correo y teléfono de contacto y al menos
    ANTICIPACION_MINIMA_HORAS de anticipación."""
    if not permitir_duplicado_cliente:
        for cita_existente, serv_existente in citas_activas_cliente(db, id_negocio, id_cliente, ahora_local):
            if (
                cita_existente.fecha_cita == fecha
                and cita_existente.hora_cita.replace(second=0, microsecond=0) == hora.replace(second=0, microsecond=0)
                and serv_existente.id_servicio == servicio.id_servicio
            ):
                raise CitaActivaExistenteError(cita_existente, serv_existente)

    nombre_contacto, correo_contacto, telefono_contacto = validar_contacto(
        nombre_contacto, correo_contacto, telefono_contacto
    )
    validar_anticipacion(fecha, hora, ahora_local)

    validar_dentro_de_horario(horario_negocio, fecha, hora, duracion_minutos(servicio))
    validar_sin_choque_propio(db, id_negocio, id_cliente, fecha, hora, duracion_minutos(servicio))
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
    id_agenda: int | None = None,
) -> Agenda:
    """Mueve UNA cita activa del cliente a un nuevo día/hora (y opcionalmente
    a otro servicio). Las demás citas del cliente no se tocan.

    Cuál se mueve lo decide elegir_cita_cliente (por id, porque es la única,
    o por el servicio nombrado); si hay varias y no se sabe cuál, lanza
    CitaAmbiguaError. La nueva hora no puede chocar con otras citas del
    negocio ni con las otras citas del mismo cliente. Conserva los datos de
    contacto; los recordatorios se reprograman para la nueva hora."""
    validar_anticipacion(nueva_fecha, nueva_hora, ahora_local)
    cita, servicio_actual = elegir_cita_cliente(
        db, id_negocio, id_cliente, id_agenda=id_agenda, servicio=nuevo_servicio
    )
    servicio_final = nuevo_servicio or servicio_actual
    duracion = duracion_minutos(servicio_final)

    validar_dentro_de_horario(horario_negocio, nueva_fecha, nueva_hora, duracion)
    validar_sin_choque_propio(
        db, id_negocio, id_cliente, nueva_fecha, nueva_hora, duracion, excluir_id_agenda=cita.id_agenda
    )
    validar_horario_libre(db, id_negocio, nueva_fecha, nueva_hora, duracion, excluir_id_agenda=cita.id_agenda)

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


def actualizar_contacto_cita_activa(
    db: Session,
    id_negocio: int,
    id_cliente: int,
    nombre: str | None = None,
    correo: str | None = None,
    telefono: str | None = None,
) -> tuple[Agenda, Servicio, dict[str, str]]:
    """Cambia el nombre, correo y/o teléfono de contacto de la cita activa
    del cliente (a ellos se envían los recordatorios y la confirmación).
    Solo se tocan los datos que vienen; devuelve (cita, servicio, cambios)
    con los valores nuevos que efectivamente cambiaron.

    Lanza SinCitaActivaError si no tiene cita activa y DatosContactoError
    (con `invalidos`) si el correo o el teléfono no son válidos."""
    activas = citas_activas_cliente(db, id_negocio, id_cliente)
    if not activas:
        raise SinCitaActivaError("No tienes una cita activa.")
    cita, servicio = activas[0]

    invalidos, nuevos = [], {}
    if nombre is not None and nombre.strip():
        nombre_ok = " ".join(nombre.split())[:100]
        if len(nombre_ok) < 2:
            invalidos.append("nombre")
        else:
            nuevos["nombre"] = nombre_ok
    if correo is not None and correo.strip():
        correo_ok = normalizar_correo(correo)
        if correo_ok is None:
            invalidos.append("correo")
        else:
            nuevos["correo"] = correo_ok
    if telefono is not None and telefono.strip():
        telefono_ok = normalizar_telefono(telefono)
        if telefono_ok is None:
            invalidos.append("teléfono")
        else:
            nuevos["teléfono"] = telefono_ok
    if invalidos:
        raise DatosContactoError([], invalidos)

    # Con varias citas activas, el cambio aplica a todas (los recordatorios
    # de cada una llegan al mismo contacto).
    cambios: dict[str, str] = {}
    for c, _ in activas:
        actuales = {"nombre": c.nombre_contacto, "correo": c.correo_contacto, "teléfono": c.telefono_contacto}
        for campo, valor in nuevos.items():
            if valor != actuales[campo]:
                cambios[campo] = valor
        if "nombre" in nuevos:
            c.nombre_contacto = nuevos["nombre"]
        if "correo" in nuevos:
            c.correo_contacto = nuevos["correo"]
        if "teléfono" in nuevos:
            c.telefono_contacto = nuevos["teléfono"]

    # La ficha del cliente también se actualiza para las próximas reservas
    # (el teléfono de la ficha es su WhatsApp: ese no se cambia).
    cliente = db.query(Cliente).filter(Cliente.id_cliente == id_cliente).first()
    if cliente is not None:
        if "nombre" in cambios:
            cliente.nombre_cliente = cambios["nombre"]
        if "correo" in cambios:
            cliente.correo = cambios["correo"]
    db.commit()
    db.refresh(cita)
    return cita, servicio, cambios


def cancelar_cita_activa(
    db: Session, id_negocio: int, id_cliente: int, id_agenda: int | None = None,
    servicio: Servicio | None = None,
) -> Agenda:
    """Cancela UNA cita activa del cliente (la indicada, la única o la del
    servicio nombrado) y libera el bloque horario. Lanza CitaAmbiguaError
    si tiene varias y no se sabe cuál."""
    cita, _ = elegir_cita_cliente(db, id_negocio, id_cliente, id_agenda=id_agenda, servicio=servicio)
    cita.estado_cita = "cancelada"
    db.commit()
    db.refresh(cita)
    return cita
