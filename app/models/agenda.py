from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Date, Time, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class Agenda(Base):
    """Reservas/citas agendadas, ya sea por la IA o manualmente."""

    __tablename__ = "agenda"

    id_agenda = Column(Integer, primary_key=True, index=True)
    id_cliente = Column(Integer, ForeignKey("cliente.id_cliente"), nullable=False)
    id_servicio = Column(Integer, ForeignKey("servicio.id_servicio"), nullable=False)
    fecha_cita = Column(Date, nullable=False)
    hora_cita = Column(Time, nullable=False)
    estado_cita = Column(String(50), default="pendiente")
    # Momento (UTC) en que se creó o se reactivó como pendiente. Sirve para
    # liberar el horario si nadie la confirma en 2 horas.
    fecha_creacion = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    cliente = relationship("Cliente", back_populates="citas")
    servicio = relationship("Servicio")
