"""Schemas de entrada/salida para la agenda de citas."""
from datetime import date, datetime, time
from typing import Literal
from pydantic import BaseModel, Field

EstadoCita = Literal["pendiente", "confirmada", "cancelada", "completada", "no_asistio", "expirada"]


class CitaCreate(BaseModel):
    """Nombre, correo y teléfono son obligatorios: a ellos se envían los
    recordatorios y la confirmación de la cita."""
    telefono_cliente: str = Field(min_length=8, max_length=20)
    nombre_cliente: str = Field(min_length=2, max_length=100)
    correo_cliente: str = Field(min_length=5, max_length=100)
    id_servicio: int
    fecha_cita: date
    hora_cita: time


class CitaUpdate(BaseModel):
    """Editar una cita desde la Agenda: solo se cambian los campos enviados."""
    id_servicio: int | None = None
    fecha_cita: date | None = None
    hora_cita: time | None = None
    nombre_cliente: str | None = Field(default=None, min_length=2, max_length=100)
    correo_cliente: str | None = Field(default=None, min_length=5, max_length=100)
    telefono_contacto: str | None = Field(default=None, min_length=8, max_length=20)


class CitaEstadoUpdate(BaseModel):
    estado_cita: EstadoCita


class CitaResponse(BaseModel):
    id_agenda: int
    id_cliente: int
    nombre_cliente: str | None
    telefono_cliente: str
    id_servicio: int
    nombre_servicio: str
    fecha_cita: date
    hora_cita: time
    estado_cita: str
    correo_cliente: str | None = None
    telefono_contacto: str | None = None
    # Seguimiento de notificaciones para mostrar en la Agenda
    recordatorio_24h_enviado: datetime | None = None
    confirmacion_1h_enviada: datetime | None = None
    respuesta_cliente: str | None = None
