"""Schemas de entrada/salida para la agenda de citas."""
from datetime import date, time
from typing import Literal
from pydantic import BaseModel, Field

EstadoCita = Literal["pendiente", "confirmada", "cancelada", "completada", "expirada"]


class CitaCreate(BaseModel):
    telefono_cliente: str = Field(min_length=8, max_length=20)
    nombre_cliente: str | None = Field(default=None, max_length=100)
    id_servicio: int
    fecha_cita: date
    hora_cita: time


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
