from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Time

from app.db.database import Base


class Bloqueo(Base):
    """Horario en que el negocio NO atiende, además de su horario normal.

    - tipo "fechas": de fecha_desde a fecha_hasta (vacaciones, feriado). Si
      trae hora_desde/hora_hasta, solo bloquea ese tramo de cada uno de esos
      días (ej. "el 24-12 cerramos a las 14:00").
    - tipo "semanal": todas las semanas, en los dias_semana indicados
      ("0,1,2,3,4" = lunes a viernes), de hora_desde a hora_hasta (almuerzo).
    """

    __tablename__ = "bloqueo"

    id_bloqueo = Column(Integer, primary_key=True, index=True)
    id_negocio = Column(Integer, ForeignKey("negocio.id_negocio"), nullable=False, index=True)
    tipo = Column(String(10), nullable=False)  # fechas | semanal
    fecha_desde = Column(Date, nullable=True)
    fecha_hasta = Column(Date, nullable=True)
    hora_desde = Column(Time, nullable=True)
    hora_hasta = Column(Time, nullable=True)
    dias_semana = Column(String(20), nullable=True)
    motivo = Column(String(100), nullable=True)
    fecha_creacion = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
