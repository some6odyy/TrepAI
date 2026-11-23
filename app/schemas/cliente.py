"""Schemas de la vista Clientes (fichas por negocio)."""
from datetime import date, datetime, time

from pydantic import BaseModel, Field


class CitaDeCliente(BaseModel):
    id_agenda: int
    fecha_cita: date
    hora_cita: time
    nombre_servicio: str
    estado_cita: str


class ClienteResumen(BaseModel):
    id_cliente: int
    nombre: str | None
    telefono: str
    correo: str | None
    total_citas: int = 0
    completadas: int = 0
    inasistencias: int = 0
    canceladas: int = 0
    ultima_visita: date | None = None
    proxima_cita: CitaDeCliente | None = None
    ultima_actividad: datetime | None = None
    notas: str | None = None


class ClienteDetalle(ClienteResumen):
    citas: list[CitaDeCliente] = []
    id_conversacion: int | None = None


class NotasUpdate(BaseModel):
    notas: str = Field(default="", max_length=4000)
