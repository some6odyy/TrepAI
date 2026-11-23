"""
Horarios bloqueados por el negocio: vacaciones, feriados, almuerzo.

agenda_service.validar_horario_libre los revisa, así que aplican a todo lo
que agenda: el formulario del Dashboard, la edición de citas, el bot con IA,
el bot sin IA y las sugerencias de horas libres.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.bloqueo import Bloqueo

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def dias_de(bloqueo: Bloqueo) -> set[int]:
    return {int(d) for d in (bloqueo.dias_semana or "").split(",") if d.strip().isdigit()}


def aplica_el_dia(bloqueo: Bloqueo, fecha: date) -> bool:
    if bloqueo.fecha_desde and fecha < bloqueo.fecha_desde:
        return False
    if bloqueo.fecha_hasta and fecha > bloqueo.fecha_hasta:
        return False
    if bloqueo.tipo == "semanal":
        return fecha.weekday() in dias_de(bloqueo)
    return True


def tramos_del_dia(db: Session, id_negocio: int, fecha: date) -> list[tuple[datetime, datetime, Bloqueo]]:
    """Intervalos bloqueados de ese día, como (inicio, fin, bloqueo)."""
    candidatos = (
        db.query(Bloqueo)
        .filter(
            Bloqueo.id_negocio == id_negocio,
            or_(Bloqueo.fecha_desde.is_(None), Bloqueo.fecha_desde <= fecha),
            or_(Bloqueo.fecha_hasta.is_(None), Bloqueo.fecha_hasta >= fecha),
        )
        .all()
    )
    tramos = []
    for b in candidatos:
        if not aplica_el_dia(b, fecha):
            continue
        if b.hora_desde is None or b.hora_hasta is None:
            ini = datetime.combine(fecha, time(0, 0))
            tramos.append((ini, ini + timedelta(days=1), b))
        else:
            tramos.append((datetime.combine(fecha, b.hora_desde), datetime.combine(fecha, b.hora_hasta), b))
    return tramos


def _dias_legibles(dias: list[int]) -> str:
    """[0..5] -> "De lunes a sábado"; [0, 2, 4] -> "Lunes, miércoles y viernes"."""
    if len(dias) == 7:
        return "Todos los días"
    if len(dias) >= 3 and dias == list(range(dias[0], dias[-1] + 1)):
        return f"De {DIAS[dias[0]]} a {DIAS[dias[-1]]}"
    nombres = [DIAS[d] for d in dias]
    texto = nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
    return texto[:1].upper() + texto[1:]


def describir(bloqueo: Bloqueo) -> str:
    """Texto legible: "Lun a vie, 14:00-15:00" / "24-12 al 26-12 (todo el día)"."""
    horas = (
        f"{bloqueo.hora_desde:%H:%M}-{bloqueo.hora_hasta:%H:%M}"
        if bloqueo.hora_desde and bloqueo.hora_hasta else "todo el día"
    )
    if bloqueo.tipo == "semanal":
        return f"{_dias_legibles(sorted(dias_de(bloqueo)))}, {horas}"
    if bloqueo.fecha_desde == bloqueo.fecha_hasta or bloqueo.fecha_hasta is None:
        return f"{bloqueo.fecha_desde:%d-%m-%Y}, {horas}"
    return f"Del {bloqueo.fecha_desde:%d-%m-%Y} al {bloqueo.fecha_hasta:%d-%m-%Y}, {horas}"
