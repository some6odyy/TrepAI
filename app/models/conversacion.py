from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.db.database import Base


class Conversacion(Base):
    """Hilo de conversación entre un cliente y el bot de un negocio."""

    __tablename__ = "conversacion"

    id_conversacion = Column(Integer, primary_key=True, index=True)
    id_cliente = Column(Integer, ForeignKey("cliente.id_cliente"), nullable=False)
    id_negocio = Column(Integer, ForeignKey("negocio.id_negocio"), nullable=False)
    fecha_inicio = Column(DateTime, server_default=func.now())
    fecha_fin = Column(DateTime, nullable=True)
    estado = Column(String(50), default="abierta")

    cliente = relationship("Cliente", back_populates="conversaciones")
    negocio = relationship("Negocio", back_populates="conversaciones")
    mensajes = relationship("Mensaje", back_populates="conversacion")


class Mensaje(Base):
    """Cada mensaje individual dentro de una conversación (RF-05: historial
    auditable de logs)."""

    __tablename__ = "mensaje"

    id_mensaje = Column(Integer, primary_key=True, index=True)
    id_conversacion = Column(Integer, ForeignKey("conversacion.id_conversacion"), nullable=False)
    emisor = Column(String(20), nullable=False)  # "cliente" | "bot" | "negocio" (respuesta manual del Dashboard)
    contenido = Column(Text, nullable=False)
    fecha_hora = Column(DateTime, server_default=func.now())
    tipo_mensaje = Column(String(20), default="texto")

    conversacion = relationship("Conversacion", back_populates="mensajes")
