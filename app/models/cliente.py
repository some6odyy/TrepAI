from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.orm import relationship

from app.db.database import Base


class Cliente(Base):
    """Cliente final que escribe por WhatsApp (usuario que se atiende con
    el barbero). Se identifica por su número de teléfono."""

    __tablename__ = "cliente"

    id_cliente = Column(Integer, primary_key=True, index=True)
    nombre_cliente = Column(String(100))
    telefono = Column(String(20), unique=True, nullable=False, index=True)
    # Correo para recordatorios y confirmaciones de citas (obligatorio al agendar).
    correo = Column(String(100), nullable=True)
    fecha_registro = Column(DateTime, server_default=func.now())

    conversaciones = relationship("Conversacion", back_populates="cliente")
    citas = relationship("Agenda", back_populates="cliente")
