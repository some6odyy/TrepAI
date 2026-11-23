"""Schemas de salida para el historial auditable de chats (RF-05)."""
from datetime import datetime
from pydantic import BaseModel, Field


class MensajeResponse(BaseModel):
    id_mensaje: int
    emisor: str
    contenido: str
    fecha_hora: datetime
    tipo_mensaje: str
    latencia_ms: int | None = None
    origen_respuesta: str | None = None

    model_config = {"from_attributes": True}


class ConversacionResponse(BaseModel):
    id_conversacion: int
    id_cliente: int
    nombre_cliente: str | None
    telefono_cliente: str
    fecha_inicio: datetime
    fecha_fin: datetime | None
    estado: str
    total_mensajes: int
    ultima_actividad: datetime | None = None
    # WhatsApp solo deja responder con texto libre si el cliente escribió en las últimas 24 h
    puede_responder: bool = False
    # Tomar el control: hasta cuándo el bot está callado en esta conversación (UTC)
    bot_pausado_hasta: datetime | None = None


class ResponderRequest(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


class PausaBotRequest(BaseModel):
    pausado: bool
    horas: float | None = Field(default=None, gt=0, le=24 * 30, description="Por defecto PAUSA_BOT_MANUAL_HORAS")
