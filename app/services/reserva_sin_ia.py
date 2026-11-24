"""
Agendar SIN IA: junta los datos de la reserva a lo largo de la conversación.

Cuando la IA no está disponible, el cliente igual puede reservar escribiendo
de forma natural, en uno o varios mensajes:

    "Degradado con diseño"            -> servicio
    "Mañana a las 18"                 -> fecha y hora
    "Yohel / yohel@gmail.com / 967158624" -> nombre, correo y teléfono

Se leen los mensajes del cliente desde la última reserva hecha (para no
reutilizar datos viejos); si un dato aparece varias veces, gana el más
reciente. Con todo completo se usa el mismo flujo que el agendado con IA
(agendado_ia.procesar_solicitud): mismas reglas de horario, anticipación
mínima, bloqueo de horas ocupadas y sugerencias de horas libres.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.services import agenda_service

_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_DIAS_LEGIBLES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Palabras que nunca son un nombre de persona
_NO_NOMBRE = set(_DIAS) | {
    "hola", "holi", "buenas", "buenos", "gracias", "ok", "okay", "si", "no", "dale", "listo", "manana",
    "hoy", "pasado", "quiero", "agendar", "reservar", "hora", "cita", "para", "las", "el", "la", "a", "de",
    "catalogo", "servicios", "precio", "precios", "horario", "direccion", "corte", "barba", "con", "y",
    "perfecto", "genial", "por", "favor", "tarde", "noche", "me", "mi", "un", "una", "cuanto", "donde",
    "que", "como", "cual", "quien", "valor", "sale", "cuesta", "tienen", "hay", "horas", "disponible",
    "agenda", "confirmo", "cancelo", "necesito", "saber", "quisiera", "puedo", "bien", "vale", "buen",
    "dia", "tardes", "noches", "info", "informacion", "ya", "claro", "jaja", "jajaja", "ubicacion",
    "local", "atienden", "abren", "cierran", "nada", "otro", "otra", "tambien", "solo",
    # etiquetas de datos ("correo: ...", "nombre: ...") y frases comunes
    "nombre", "correo", "mail", "email", "gmail", "telefono", "numero", "celular", "fono", "whatsapp", "wsp",
    "falta", "faltan", "algo", "mas", "cambiar", "cambia", "cambio", "actualizar", "actualiza", "nuevo",
    "nueva", "este", "esta", "ese", "esa", "mismo", "misma", "datos", "dato", "recordatorio", "recordatorios",
}
# Palabras que indican que el mensaje es una consulta, no un nombre (ej. "cuanto sale")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_RE_TEL = re.compile(r"(?<![\d:/])\+?\d[\d \-]{7,16}\d(?![\d:/])")
_RE_NOMBRE_EXPLICITO = re.compile(
    r"\b(?:me llamo|mi nombre es|soy|a nombre de|nombre\s*:|el nombre es|cambia(?:r)? (?:mi |el )?nombre (?:a|por))\s*([a-zñ ]{2,60})",
    re.IGNORECASE,
)
# "mi teléfono es ...", "número: ...", "celular 9..." (para avisar si el número no es válido)
_RE_TEL_MENCIONADO = re.compile(
    r"\b(?:telefono|numero|celular|fono|whatsapp|wsp|cel)\b[^\d+]{0,25}(\+?\d[\d \-]{3,20}\d)"
)
# Intención de cambiar datos de contacto ("cambia mi número", "mejor a este correo")
_RE_INTENCION_CAMBIO = re.compile(
    r"\b(cambi\w*|actualiz\w*|corrig\w*|modific\w*|otro|otra|nuevo|nueva|mejor|en vez|envia\w*|manda\w*|"
    r"mi (?:telefono|numero|celular|correo|mail|email|nombre)|(?:telefono|numero|celular|correo|nombre) es)\b"
)
_RE_ESTE_NUMERO = re.compile(r"\b(?:este|al|mi|el) (?:mismo )?(?:numero|whatsapp|wsp|celular)(?: (?:de )?(?:aca|aqui|desde el que (?:te )?escribo))?\b|\beste mismo\b")


# Intención de mover una hora que ya tiene / de cancelarla (texto normalizado)
RE_CAMBIO_HORA = re.compile(
    r"\b(cambi\w*|mov\w*|muev\w*|reagend\w*|reprogram\w*|corr(?:e|er|ela|erla|amos)|pas(?:a|ar|ala|arla|emos))\b"
)
RE_TODAS = re.compile(r"\b(todas|todos|las dos|ambas|las \d horas)\b")
RE_CANCELAR = re.compile(r"\b(cancel\w*|anul\w*|ya no (?:voy|podre|puedo))\b")
# "2", "la 2", "opción 1" como respuesta a la lista de citas (mensaje completo)
RE_NUMERO_LISTA = re.compile(r"^\s*(?:la|el|opcion|numero|#)?\s*([1-9])\s*$")
# Palabras de un cambio de datos de contacto (no de hora)
RE_DATO_CONTACTO = re.compile(r"\b(correo|mail|email|telefono|numero|celular|fono|nombre)\b")


def normalizar_texto(texto: str) -> str:
    return _normalizar(texto).replace("ñ", "n")


def _normalizar(texto: str) -> str:
    # Se conserva la ñ (para nombres) pero se quitan tildes
    t = (texto or "").lower().replace("ñ", "\0")
    t = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().replace("\0", "ñ")
    return re.sub(r"[ \t]+", " ", t)


@dataclass
class DatosReserva:
    servicio: object | None = None  # app.models.servicio.Servicio
    fecha: date | None = None
    hora: time | None = None
    nombre: str | None = None
    correo: str | None = None
    telefono: str | None = None
    faltan: list[str] = field(default_factory=list)

    def calcular_faltantes(self) -> list[str]:
        self.faltan = [
            campo for campo, valor in (
                ("servicio", self.servicio), ("día", self.fecha), ("hora", self.hora),
                ("nombre", self.nombre), ("correo", self.correo), ("teléfono", self.telefono),
            ) if not valor
        ]
        return self.faltan

    @property
    def hay_algo_de_reserva(self) -> bool:
        return bool(self.fecha or self.hora)


# ----------------------------------------------------------------- extractores

_RE_HORA_ETIQUETA = re.compile(r"\bhora\s*:?\s*(\d{1,2})(?:[:.](\d{2}))?\b(?!\s*(?:[/-]|de\s))")
_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
# 21-11, 21/11, "21 - 11", "29 - 11 - 2027", 29/11/27
_RE_FECHA_NUM = re.compile(r"\b(\d{1,2})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*(\d{4}|\d{2}))?\b(?![:.]\d)")
# "29 de noviembre", "29 de noviembre de 2027"
_RE_FECHA_MES = re.compile(r"\b(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\b(?:\s+(?:de|del)\s+(\d{4}))?")
# Si piden una fecha sin año que ya pasó hace poco, es un error (no el próximo año)
DIAS_FECHA_RECIENTE = 60


def extraer_hora(texto: str) -> time | None:
    t = _normalizar(texto).replace("ñ", "n")
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm|hrs?|horas)?\b", t)
    if m:
        h, mi, sufijo = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.search(
            r"\ba las (\d{1,2})(?:\s*y\s*(media|cuarto))?(?:\s*(hrs?|horas|h|am|pm))?"
            r"(?:\s*de la (tarde|noche|manana))?\b", t
        ) or re.search(r"\b(\d{1,2})\s*(?:y\s*(media|cuarto)\s*)?(hrs?|horas|h|am|pm)\b(?:\s*de la (tarde|noche|manana))?", t)
        if not m:
            # "hora: 15", "hora 15:30"
            et = _RE_HORA_ETIQUETA.search(t)
            if not et:
                return None
            h, mi, sufijo = int(et.group(1)), int(et.group(2) or 0), ""
        else:
            h = int(m.group(1))
            mi = {"media": 30, "cuarto": 15}.get(m.group(2) or "", 0)
            sufijo = (m.group(3) or "") + " " + (m.group(4) or "")
    sufijo = sufijo or ""
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    if ("pm" in sufijo or "tarde" in sufijo or "noche" in sufijo) and h < 12:
        h += 12
    elif "am" not in sufijo and "manana" not in sufijo and 1 <= h <= 8:
        h += 12  # "a las 6" en un negocio de atención = 18:00
    return time(h, mi)


def hora_invalida_mencionada(texto: str) -> bool:
    """"hora : 25", "a las 24", "25:00": una hora que no existe."""
    t = _normalizar(texto).replace("ñ", "n")
    for m in re.finditer(r"\b(?:hora\s*:?|a las)\s*(\d{1,2})(?:[:.](\d{2}))?\b", t):
        if int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
            return True
    return any(int(h) > 23 or int(mi) > 59 for h, mi in re.findall(r"\b(\d{1,2})[:](\d{2})\b", t))


def fecha_legible(fecha: date, hoy: date | None = None) -> str:
    """"sábado 28-11" (con el año si no es el actual: "lunes 29-11-2027")."""
    anio = "" if fecha.year == (hoy or date.today()).year else f"-{fecha.year}"
    return f"{_DIAS_LEGIBLES[fecha.weekday()]} {fecha:%d-%m}{anio}"


def _dia_semana_nombrado(t: str) -> int | None:
    for i, nombre in enumerate(_DIAS):
        if re.search(rf"\b{nombre}\b", t):
            return i
    return None


def analizar_fecha(texto: str, hoy: date) -> tuple[date | None, str | None]:
    """Devuelve (fecha, problema). Si la fecha pedida ya pasó o no calza con
    el día de la semana nombrado ("sábado 29-11-2027" cae lunes), la fecha
    es None y `problema` trae el mensaje para el cliente."""
    t = _normalizar(texto).replace("ñ", "n")
    if re.search(r"\bpasado manana\b", t):
        return hoy + timedelta(days=2), None
    # "mañana" como día (no "de/en la mañana")
    if re.search(r"(?<!la )\bmanana\b", t):
        return hoy + timedelta(days=1), None
    if re.search(r"\bhoy\b", t):
        return hoy, None

    dia_semana = _dia_semana_nombrado(t)
    m = _RE_FECHA_NUM.search(t)
    m_mes = _RE_FECHA_MES.search(t)
    if m or m_mes:
        if m_mes:
            dia, mes, anio_txt = int(m_mes.group(1)), _MESES[m_mes.group(2)], m_mes.group(3)
        else:
            dia, mes, anio_txt = int(m.group(1)), int(m.group(2)), m.group(3)
        anio = int(anio_txt) if anio_txt else hoy.year
        if anio < 100:
            anio += 2000
        try:
            f = date(anio, mes, dia)
        except ValueError:
            return None, None
        if f < hoy:
            if anio_txt or (hoy - f).days <= DIAS_FECHA_RECIENTE:
                problema = f"El {fecha_legible(f, hoy)} ya pasó 🙈 ¿Para qué día la quieres?"
                if dia_semana is not None:
                    proximo = hoy + timedelta(days=((dia_semana - hoy.weekday()) % 7) or 7)
                    problema = (
                        f"El {fecha_legible(f, hoy)} ya pasó 🙈 ¿La quieres para el próximo "
                        f"{_DIAS_LEGIBLES[dia_semana]}, el {proximo:%d-%m}, o para otro día?"
                    )
                return None, problema
            f = date(anio + 1, mes, dia)  # "05-01" pedido en noviembre = enero del próximo año
        if dia_semana is not None and dia_semana != f.weekday():
            # El día de la semana nombrado más cercano a esa fecha
            opciones = [f + timedelta(days=k) for k in range(-3, 4) if (f + timedelta(days=k)).weekday() == dia_semana]
            alternativa = min((o for o in opciones if o >= hoy), key=lambda o: abs((o - f).days), default=None)
            otra = f" o al *{fecha_legible(alternativa, hoy)}*" if alternativa else ""
            return None, (
                f"El {f:%d-%m}{'' if f.year == hoy.year else f'-{f.year}'} cae *{_DIAS_LEGIBLES[f.weekday()]}*, "
                f"no {_DIAS_LEGIBLES[dia_semana]} 🤔 ¿Te refieres al *{fecha_legible(f, hoy)}*{otra}?"
            )
        return f, None

    if dia_semana is not None:
        dias = (dia_semana - hoy.weekday()) % 7
        return hoy + timedelta(days=dias or 7), None
    m = re.search(r"\b(?:el|dia)\s+(\d{1,2})\b(?!\s*(?::|hrs?|horas|h\b|am|pm))", t)
    if m:
        dia = int(m.group(1))
        for meses in (0, 1):
            anio, mes = hoy.year + (hoy.month + meses - 1) // 12, (hoy.month + meses - 1) % 12 + 1
            try:
                f = date(anio, mes, dia)
            except ValueError:
                continue
            if f >= hoy:
                return f, None
    return None, None


def extraer_fecha(texto: str, hoy: date) -> date | None:
    return analizar_fecha(texto, hoy)[0]


def problema_fecha(texto: str, hoy: date) -> str | None:
    return analizar_fecha(texto, hoy)[1]


def extraer_correo(texto: str) -> str | None:
    m = _RE_EMAIL.search(texto or "")
    return agenda_service.normalizar_correo(m.group(0)) if m else None


def extraer_telefono(texto: str) -> str | None:
    sin_correo = _RE_EMAIL.sub(" ", texto or "")
    for m in _RE_TEL.finditer(sin_correo):
        tel = agenda_service.normalizar_telefono(m.group(0))
        if tel:
            return tel
    return None


def telefono_invalido_mencionado(texto: str) -> bool:
    """True si el cliente dio explícitamente un teléfono que no es válido
    (ej. "mi teléfono es 11111111"), para pedírselo de nuevo."""
    t = _normalizar(_RE_EMAIL.sub(" ", texto or "")).replace("ñ", "n")
    m = _RE_TEL_MENCIONADO.search(t)
    return bool(m) and agenda_service.normalizar_telefono(m.group(1)) is None and extraer_telefono(texto) is None


@dataclass
class CambioContacto:
    nombre: str | None = None
    correo: str | None = None
    telefono: str | None = None
    telefono_invalido: bool = False

    @property
    def hay_algo(self) -> bool:
        return bool(self.nombre or self.correo or self.telefono or self.telefono_invalido)


def extraer_cambio_contacto(texto: str, servicios: list, hoy: date, whatsapp_cliente: str | None) -> CambioContacto | None:
    """Para un cliente que YA tiene una hora agendada: ¿el mensaje trae un
    teléfono, correo o nombre nuevo para los recordatorios? Ej.:
        "mi teléfono es +56 9 8140 7529"   "cambia mi correo a ana@gmail.com"
        "mejor mándalo a este número"     "a nombre de Ignacio Pérez"
    Devuelve None si el mensaje es otra cosa (o trae día/hora/servicio, que
    sería un cambio de hora y no de datos)."""
    t = _normalizar(texto).replace("ñ", "n")
    if extraer_fecha(texto, hoy) or extraer_hora(texto) or extraer_servicio(texto, servicios):
        return None
    cambio = CambioContacto(correo=extraer_correo(texto), telefono=extraer_telefono(texto))
    m = _RE_NOMBRE_EXPLICITO.search(_normalizar(texto))
    if m:
        cambio.nombre = extraer_nombre(texto)
    if not cambio.telefono and whatsapp_cliente and _RE_ESTE_NUMERO.search(t) and _RE_INTENCION_CAMBIO.search(t):
        cambio.telefono = whatsapp_cliente
    if not cambio.telefono and telefono_invalido_mencionado(texto):
        cambio.telefono_invalido = True
    return cambio if cambio.hay_algo else None


def extraer_nombre(texto: str, excluir: set[str] | None = None) -> str | None:
    """Nombre dicho explícitamente ("me llamo Juan") o una línea que solo
    tiene 1 a 4 palabras que parecen nombre ("Yohel", "Juan Pérez")."""
    m = _RE_NOMBRE_EXPLICITO.search(_normalizar(texto))
    if m:
        candidato = m.group(1).strip()
        palabras = [p for p in candidato.split() if p not in _NO_NOMBRE][:4]
        if palabras:
            return _capitalizar_desde_original(" ".join(palabras), texto)
    for linea in re.split(r"[\n,;]+", texto or ""):
        limpia = _RE_EMAIL.sub("", linea)
        limpia = re.sub(r"\+?\d[\d \-]*", "", limpia).strip(" ,;:-/")
        palabras = limpia.split()
        if not 1 <= len(palabras) <= 4:
            continue
        if not all(re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'.]{2,}", p) for p in palabras):
            continue
        if any(_normalizar(p).replace("ñ", "n") in _NO_NOMBRE or _normalizar(p) in (excluir or set()) for p in palabras):
            continue
        # La línea entera tiene que ser el nombre (ej. "Yohel"), no parte de una frase larga
        if len(re.findall(r"\w+", linea)) - len(re.findall(r"\d+", linea)) > 6:
            continue
        return " ".join(p[:1].upper() + p[1:] for p in palabras)
    return None


def _capitalizar_desde_original(normalizado: str, original: str) -> str:
    m = re.search(re.escape(normalizado), _normalizar(original))
    texto = original[m.start():m.end()] if m else normalizado
    return " ".join(p[:1].upper() + p[1:] for p in texto.split())


def extraer_servicio(texto: str, servicios: list):
    """El servicio nombrado en el texto; si calzan varios, el de nombre más largo."""
    t = _normalizar(texto)
    candidatos = [s for s in servicios if _normalizar(s.nombre_servicio).strip() and _normalizar(s.nombre_servicio) in t]
    return max(candidatos, key=lambda s: len(s.nombre_servicio)) if candidatos else None


# ------------------------------------------------------------------ armado

def reunir_datos(mensajes_cliente: list[str], servicios: list, hoy: date) -> DatosReserva:
    """Recorre los mensajes del cliente en orden; el dato más reciente gana."""
    datos = DatosReserva()
    # Las palabras de los servicios ("Degradado", "Barba") nunca son un nombre
    palabras_servicios = {w for s in servicios for w in _normalizar(s.nombre_servicio).split()}
    for texto in mensajes_cliente:
        datos.servicio = extraer_servicio(texto, servicios) or datos.servicio
        datos.fecha = extraer_fecha(texto, hoy) or datos.fecha
        datos.hora = extraer_hora(texto) or datos.hora
        datos.correo = extraer_correo(texto) or datos.correo
        datos.telefono = extraer_telefono(texto) or datos.telefono
        datos.nombre = extraer_nombre(texto, palabras_servicios) or datos.nombre
    datos.calcular_faltantes()
    return datos


_AFIRMATIVOS = {"ok", "okay", "oki", "si", "sip", "dale", "ya", "bueno", "perfecto", "listo", "claro", "va", "sale", "obvio", "ese", "esa"}
# "¿La quieres para el próximo sábado, el 28-11, o para otro día?"
_RE_PROPUESTA = re.compile(r"pr[oó]ximo \w+, el (\d{2}-\d{2}(?:-\d{4})?)")


def es_afirmativo(texto: str) -> bool:
    palabras = re.findall(r"[a-zñ]+", _normalizar(texto))
    return bool(palabras) and len(palabras) <= 4 and palabras[0] in _AFIRMATIVOS


def fecha_propuesta(texto_bot: str) -> str | None:
    """La fecha que el bot propuso al decir que la pedida ya pasó."""
    m = _RE_PROPUESTA.search(texto_bot or "")
    return m.group(1) if m else None


def mensajes_desde_ultima_reserva(historial: list[tuple[str, str]], mensaje_actual: str) -> list[str]:
    """Mensajes del cliente posteriores a la última reserva/cancelación hecha
    por el bot, más el actual. Un "ok"/"sí" justo después de que el bot
    propuso otra fecha ("ya pasó... ¿el próximo sábado, el 28-11?") cuenta
    como esa fecha."""
    ultimo_corte = -1
    for i, (emisor, contenido) in enumerate(historial):
        if emisor != "cliente" and ("Reservé" in contenido or "Cancelé" in contenido or "Listo, cancelamos" in contenido):
            ultimo_corte = i
    previos, propuesta = [], None
    for emisor, contenido in historial[ultimo_corte + 1:]:
        if emisor != "cliente":
            propuesta = fecha_propuesta(contenido)
            continue
        previos.append(f"el {propuesta}" if propuesta and es_afirmativo(contenido) else contenido)
        propuesta = None
    if propuesta and es_afirmativo(mensaje_actual):
        mensaje_actual = f"el {propuesta}"
    return [*previos, mensaje_actual]


_NOMBRES_FALTA = {
    "servicio": "qué *servicio* quieres",
    "día": "el *día*",
    "hora": "la *hora*",
    "nombre": "tu *nombre*",
    "correo": "tu *correo*",
    "teléfono": "un *teléfono* para los recordatorios",
}


def _lista(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def mensaje_faltantes(datos: DatosReserva) -> str:
    anotado = []
    if datos.servicio is not None:
        anotado.append(f"*{datos.servicio.nombre_servicio}*")
    if datos.fecha:
        anotado.append(f"el {fecha_legible(datos.fecha)}")
    if datos.hora:
        anotado.append(f"a las {datos.hora:%H:%M}")
    for campo, valor in (("nombre", datos.nombre), ("correo", datos.correo), ("teléfono", datos.telefono)):
        if valor:
            anotado.append(f"{campo}: {valor}")
    inicio = f"Anoté: {', '.join(anotado)}. " if anotado else ""
    faltan = [_NOMBRES_FALTA[c] for c in datos.faltan]
    return f"{inicio}Para reservar me falta {_lista(faltan)}. 🙌"


def mensaje_aporta(texto: str, servicios: list, hoy: date) -> bool:
    """¿El mensaje actual trae algún dato de reserva? Si no (ej. "¿dónde
    quedan?"), se responde la pregunta en vez de seguir pidiendo datos."""
    palabras_servicios = {w for s in servicios for w in _normalizar(s.nombre_servicio).split()}
    return any((
        extraer_servicio(texto, servicios), extraer_fecha(texto, hoy), extraer_hora(texto),
        extraer_correo(texto), extraer_telefono(texto), extraer_nombre(texto, palabras_servicios),
    ))
