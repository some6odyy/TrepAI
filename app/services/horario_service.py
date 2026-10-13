"""
Interpreta el "horario de atención" de un negocio y valida que una cita
caiga dentro de él.

El horario se guarda como texto libre (negocio.horario, ej. "Lun-Sáb
10:00-19:00"), así que acá lo convertimos a bloques por día de la semana.

Formatos soportados (mayúsculas, tildes y puntuación no importan):
  - "Lun-Sáb 10:00-19:00"            - "Lunes a sábado, 10:00 a 19:00"
  - "Lun a Vie 9 a 18"               - "Lun, Mié y Vie 10:00-14:00"
  - "Lun-Vie 10:00-19:00, Sáb 10:00-14:00"   (varios tramos por día)
  - "Lun-Vie 10:00-14:00 y 15:00-19:00"      (horario partido)
  - "Todos los días 09:00-21:00"     - "24 horas" / "24/7"
Los tramos que cruzan la medianoche (ej. 20:00-02:00) no están soportados.
"""
import re
import unicodedata
from datetime import date, time

_DIAS = {
    "lunes": 0, "lun": 0, "martes": 1, "mar": 1,
    "miercoles": 2, "mier": 2, "mie": 2, "jueves": 3, "jue": 3,
    "viernes": 4, "vie": 4, "sabado": 5, "sab": 5, "domingo": 6, "dom": 6,
}
_NOMBRES_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

_RE_DIA = re.compile(
    r"\b(lunes|lun|martes|mar|miercoles|mier|mie|jueves|jue|viernes|vie|sabados?|sab|domingos?|dom)\b"
)
_RE_HORA = re.compile(
    r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:hrs?\.?|horas)?\s*"
    r"(?:-|–|—|a|hasta)\s*(?:las\s*)?"
    r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:hrs?\.?|horas)?"
)
_RE_CONECTOR_RANGO = re.compile(r"^(a|al|hasta|-|–|—)$")

# {dia_semana (0=lunes): [(inicio_min, fin_min), ...]}
Horario = dict[int, list[tuple[int, int]]]


class FueraDeHorarioError(Exception):
    """La cita no cabe dentro del horario de atención del negocio."""


def _normalizar(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return sin_tildes.lower()


def _indice_dia(token: str) -> int:
    return _DIAS[token if token in _DIAS else token[:-1]]  # "sabados" -> "sabado"


def _parsear_dias(prefijo: str) -> set[int] | None:
    """Días de la semana mencionados en el texto, o None si no hay ninguno."""
    if re.search(r"todos\s+los\s+dias|\bdiario\b|\bdiariamente\b", prefijo):
        return set(range(7))

    tokens = list(_RE_DIA.finditer(prefijo))
    if not tokens:
        return None

    dias: set[int] = set()
    i = 0
    while i < len(tokens):
        ini = _indice_dia(tokens[i].group(1))
        if i + 1 < len(tokens):
            entre = prefijo[tokens[i].end():tokens[i + 1].start()].strip()
            if _RE_CONECTOR_RANGO.match(entre):
                fin = _indice_dia(tokens[i + 1].group(1))
                d = ini
                while True:  # admite rangos que cruzan el domingo, ej. "sáb a lun"
                    dias.add(d)
                    if d == fin:
                        break
                    d = (d + 1) % 7
                i += 2
                continue
        dias.add(ini)
        i += 1
    return dias


def _a_minutos(h: str, m: str | None) -> int | None:
    hh, mm = int(h), int(m) if m else 0
    if hh > 24 or mm > 59 or (hh == 24 and mm != 0):
        return None
    return hh * 60 + mm


def parsear_horario(texto: str | None) -> Horario | None:
    """Convierte el texto en bloques por día. Devuelve None si el texto está
    vacío o no se pudo interpretar con seguridad."""
    if not texto or not texto.strip():
        return None
    norm = _normalizar(texto)

    coincidencias = list(_RE_HORA.finditer(norm))

    if not coincidencias:
        if re.search(r"24\s*(horas|hrs?)|24\s*/\s*7|todo\s+el\s+dia", norm):
            dias = _parsear_dias(norm) or set(range(7))
            return {d: [(0, 1440)] for d in dias}
        return None

    horario: Horario = {}
    previo = 0
    dias_previos: set[int] | None = None
    for m in coincidencias:
        ini = _a_minutos(m.group(1), m.group(2))
        fin = _a_minutos(m.group(3), m.group(4))
        if fin == 0 and ini:  # "10:00-00:00" = hasta la medianoche
            fin = 1440
        if ini is None or fin is None or fin <= ini:
            return None
        dias = _parsear_dias(norm[previo:m.start()])
        if dias is None:
            dias = dias_previos if dias_previos is not None else set(range(7))
        for d in dias:
            horario.setdefault(d, []).append((ini, fin))
        dias_previos = dias
        previo = m.end()

    # Días escritos DESPUÉS del último tramo ("10:00-19:00 lun-vie"): ambiguo.
    if _RE_DIA.search(norm[previo:]):
        return None
    return horario


def validar_dentro_de_horario(horario_texto: str | None, fecha: date, hora: time, duracion_min: int) -> None:
    """Lanza FueraDeHorarioError si la cita (inicio Y término) no cabe en el
    horario de atención. Si el negocio no configuró horario, o no se puede
    interpretar, no se bloquea nada."""
    horario = parsear_horario(horario_texto)
    if horario is None:
        return

    inicio = hora.hour * 60 + hora.minute
    fin = inicio + duracion_min
    bloques = horario.get(fecha.weekday(), [])

    if not bloques:
        raise FueraDeHorarioError(
            f"El negocio no atiende los {_NOMBRES_DIA[fecha.weekday()]} (horario: {horario_texto.strip()})"
        )
    if not any(b0 <= inicio and fin <= b1 for b0, b1 in bloques):
        raise FueraDeHorarioError(
            f"Fuera del horario de atención ({horario_texto.strip()}). "
            f"La cita debe empezar y terminar dentro del horario."
        )
