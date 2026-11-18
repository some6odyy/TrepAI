from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.db.database import Base


class Aviso(Base):
    """Avisos para el administrador que aparecen en vivo en el Dashboard
    (ej. "en 1 hora: Degradado con Juan", "Juan confirmó su hora")."""

    __tablename__ = "aviso"

    id_aviso = Column(Integer, primary_key=True, index=True)
    id_negocio = Column(Integer, ForeignKey("negocio.id_negocio"), nullable=False, index=True)
    id_agenda = Column(Integer, ForeignKey("agenda.id_agenda", ondelete="SET NULL"), nullable=True)
    tipo = Column(String(30), nullable=False)  # cita_1h | cliente_confirmo | cliente_cancelo | nueva_cita
    texto = Column(Text, nullable=False)
    fecha = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    leido = Column(Boolean, default=False, nullable=False)
