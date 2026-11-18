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


    # Datos de contacto que se piden SIEMPRE al agendar (manual o por el bot).
    # Los recordatorios y la confirmación se envían a estos datos, que pueden
    # ser distintos del número desde el que el cliente escribió.
    nombre_contacto = Column(String(100), nullable=True)
    correo_contacto = Column(String(100), nullable=True)
    telefono_contacto = Column(String(20), nullable=True)

    # Control de notificaciones (UTC). NULL = todavía no se envía. Se marcan
    # con un UPDATE atómico antes de enviar, así dos ejecuciones paralelas
    # del cron nunca mandan el mismo mensaje dos veces.
    aviso_reserva_enviado = Column(DateTime, nullable=True)
    recordatorio_24h_enviado = Column(DateTime, nullable=True)
    confirmacion_1h_enviada = Column(DateTime, nullable=True)
    # Respuesta del cliente al mensaje de 1 hora antes: "si" | "no" | NULL
    respuesta_cliente = Column(String(10), nullable=True)

    cliente = relationship("Cliente", back_populates="citas")
    servicio = relationship("Servicio")
