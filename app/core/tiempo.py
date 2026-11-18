"""Hora local de Chile. Las citas se guardan en hora local (fecha_cita /
hora_cita) y los registros técnicos en UTC, así que hace falta convertir."""
from datetime import datetime, timedelta, timezone


def ahora_chile() -> datetime:
    """Hora local de Chile (con tzinfo). En Windows zoneinfo puede no tener
    la base de zonas horarias (falta el paquete tzdata); en ese caso usamos
    UTC-3 fijo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Santiago"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-3)))


def ahora_chile_naive() -> datetime:
    """Igual que ahora_chile() pero sin tzinfo, para comparar con
    datetime.combine(fecha_cita, hora_cita)."""
    return ahora_chile().replace(tzinfo=None)


def ahora_utc() -> datetime:
    """Hora actual en UTC, sin tzinfo (así se guarda en la BD)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
