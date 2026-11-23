from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


def _ahora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EventoWhatsapp(Base):
    """ID de cada mensaje de WhatsApp ya recibido (wamid). Si el webhook
    tarda, Meta reenvía el mismo mensaje: con esto el reenvío se descarta y
    el bot no contesta ni agenda dos veces."""

    __tablename__ = "evento_whatsapp"

    wamid = Column(String(200), primary_key=True)
    fecha = Column(DateTime, nullable=False, default=_ahora, index=True)


class IntentoAcceso(Base):
    """Intentos fallidos de inicio de sesión y solicitudes de recuperación
    de contraseña, para limitar los intentos por correo y por IP."""

    __tablename__ = "intento_acceso"

    id_intento = Column(Integer, primary_key=True, index=True)
    tipo = Column(String(20), nullable=False)  # login | recuperacion
    clave = Column(String(150), nullable=False, index=True)  # "correo:x@y.cl" o "ip:1.2.3.4"
    fecha = Column(DateTime, nullable=False, default=_ahora, index=True)
