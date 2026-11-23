from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint

from app.db.database import Base


class FichaCliente(Base):
    """Notas que cada negocio guarda sobre un cliente (ej. "le gusta el 1 a
    los lados"). Un mismo cliente (número de WhatsApp) puede atenderse en
    varios negocios de TrepAI, por eso las notas van por negocio."""

    __tablename__ = "ficha_cliente"
    __table_args__ = (UniqueConstraint("id_negocio", "id_cliente", name="uq_ficha_negocio_cliente"),)

    id_ficha = Column(Integer, primary_key=True, index=True)
    id_negocio = Column(Integer, ForeignKey("negocio.id_negocio"), nullable=False, index=True)
    id_cliente = Column(Integer, ForeignKey("cliente.id_cliente"), nullable=False, index=True)
    notas = Column(Text, nullable=True)
    fecha_actualizacion = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
