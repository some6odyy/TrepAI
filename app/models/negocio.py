from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class Negocio(Base):
    """Un negocio suscrito a TrepAI (ej. Silvabarber). Aísla los datos
    entre negocios para cumplir RNF-02 (arquitectura multitenant)."""

    __tablename__ = "negocio"

    id_negocio = Column(Integer, primary_key=True, index=True)
    id_administrador = Column(Integer, ForeignKey("administrador.id_administrador"), nullable=False)
    nombre_negocio = Column(String(100), nullable=False)
    direccion = Column(String(200))
    telefono = Column(String(20))
    horario = Column(String(100))
    estado_bot = Column(Boolean, default=False)  # RF-04: interruptor on/off

    # Conexión con WhatsApp Business Cloud API (RF-06). Cada negocio tiene
    # su propio número/token — así es como identificamos a qué barbería
    # pertenece cada mensaje entrante (RNF-02: aislamiento multitenant).
    whatsapp_phone_number_id = Column(String(50), unique=True, nullable=True, index=True)
    whatsapp_token = Column(String(500), nullable=True)  # TODO: cifrar en reposo antes de producción

    administrador = relationship("Administrador", back_populates="negocios")
    servicios = relationship("Servicio", back_populates="negocio")
    conversaciones = relationship("Conversacion", back_populates="negocio")
    contexto_ia = relationship("ContextoIA", back_populates="negocio", uselist=False)
