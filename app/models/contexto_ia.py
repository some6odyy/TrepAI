from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.db.database import Base


class ContextoIA(Base):
    """Reglas de negocio e instrucciones que el administrador inyecta
    para instruir a la IA (RF-03). Se combina con el mensaje del cliente
    para armar el prompt dinámico (RF-07)."""

    __tablename__ = "contexto_ia"

    id_contexto = Column(Integer, primary_key=True, index=True)
    id_negocio = Column(Integer, ForeignKey("negocio.id_negocio"), nullable=False, unique=True)
    reglas_negocio = Column(Text)
    instrucciones = Column(Text)

    # "Funciones de sistema" del bloque de Contexto IA en el Dashboard.
    # consultar_catalogo: si está apagado, el prompt NO incluye el catálogo
    # de servicios/precios (efecto real, no decorativo).
    # agendar_automatico: si está encendido, el bot reserva horas por sí solo
    # (quedan "pendiente" hasta que el administrador las confirme). Ver
    # app/services/agendado_ia.py.
    consultar_catalogo = Column(Boolean, default=True, nullable=False)
    agendar_automatico = Column(Boolean, default=False, nullable=False)

    # Motor de IA elegido por este negocio en particular — cada pyme puede
    # preferir un proveedor/modelo distinto según su presupuesto o el nivel
    # de calidad de respuesta que necesite (RF-08).
    ai_provider = Column(String(20), default="gemini", nullable=False)
    ai_model = Column(String(60), default="gemini-3.6-flash", nullable=False)

    fecha_actualizacion = Column(DateTime, onupdate=func.now(), server_default=func.now())

    negocio = relationship("Negocio", back_populates="contexto_ia")
