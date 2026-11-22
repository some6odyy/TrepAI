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
}
# Palabras que indican que el mensaje es una consulta, no un nombre (ej. "cuanto sale")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_RE_TEL = re.compile(r"(?<![\d:/])\+?\d[\d \-]{7,16}\d(?![\d:/])")
_RE_NOMBRE_EXPLICITO = re.compile(r"\b(?:me llamo|mi nombre es|soy|a nombre de)\s+([a-zñ ]{2,60})", re.IGNORECASE)


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
            return None
        h = int(m.group(1))
        mi = {"media": 30, "cuarto": 15}.get(m.group(2) or "", 0)
        sufijo = (m.group(3) or "") + " " + (m.group(4) or "")
    sufijo = sufijo or ""
    if ("pm" in sufijo or "tarde" in sufijo or "noche" in sufijo) and h < 12:
        h += 12
    elif "am" not in sufijo and "manana" not in sufijo and 1 <= h <= 8:
        h += 12  # "a las 6" en un negocio de atención = 18:00
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return time(h, mi)


def extraer_fecha(texto: str, hoy: date) -> date | None:
    t = _normalizar(texto).replace("ñ", "n")
    if re.search(r"\bpasado manana\b", t):
        return hoy + timedelta(days=2)
    # "mañana" como día (no "de/en la mañana")
    if re.search(r"(?<!la )\bmanana\b", t):
        return hoy + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return hoy
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        anio = int(m.group(3)) if m.group(3) else hoy.year
        if anio < 100:
            anio += 2000
        try:
            f = date(anio, mes, dia)
            if not m.group(3) and f < hoy:
                f = date(anio + 1, mes, dia)
            return f
        except ValueError:
            return None
    for i, nombre in enumerate(_DIAS):
        if re.search(rf"\b{nombre}\b", t):
            dias = (i - hoy.weekday()) % 7
            return hoy + timedelta(days=dias or 7)
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
                return f
    return None


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


def mensajes_desde_ultima_reserva(historial: list[tuple[str, str]], mensaje_actual: str) -> list[str]:
    """Mensajes del cliente posteriores a la última reserva/cancelación hecha
    por el bot, más el actual."""
    ultimo_corte = -1
    for i, (emisor, contenido) in enumerate(historial):
        if emisor != "cliente" and ("Reservé" in contenido or "Cancelé" in contenido or "Listo, cancelamos" in contenido):
            ultimo_corte = i
    previos = [c for e, c in historial[ultimo_corte + 1:] if e == "cliente"]
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
        anotado.append(f"el {_DIAS_LEGIBLES[datos.fecha.weekday()]} {datos.fecha:%d-%m}")
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
