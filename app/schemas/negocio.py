"""Schemas de entrada/salida para el módulo de negocio (RF-02, RF-04)."""
from pydantic import BaseModel, Field, field_validator

from app.services.horario_service import parsear_horario

MSG_HORARIO_INVALIDO = (
    "No pude interpretar el horario. Usa un formato como 'Lun-Sáb 10:00-19:00' "
    "o 'Lun-Vie 10:00-19:00, Sáb 10:00-14:00'"
)


def _validar_horario(valor: str | None) -> str | None:
    """Rechaza horarios que el sistema no sabría interpretar, para que la
    validación de citas fuera de horario siempre tenga algo confiable."""
    if valor and valor.strip() and parsear_horario(valor) is None:
        raise ValueError(MSG_HORARIO_INVALIDO)
    return valor


class NegocioCreate(BaseModel):
    nombre_negocio: str = Field(min_length=2, max_length=100)
    direccion: str | None = Field(default=None, max_length=200)
    telefono: str | None = Field(default=None, max_length=20)
    horario: str | None = Field(default=None, max_length=100)

    @field_validator("horario")
    @classmethod
    def _horario_interpretable(cls, valor):
        return _validar_horario(valor)


class NegocioUpdate(BaseModel):
    """Todos los campos opcionales: se actualiza solo lo que venga en el body."""
    nombre_negocio: str | None = Field(default=None, min_length=2, max_length=100)
    direccion: str | None = Field(default=None, max_length=200)
    telefono: str | None = Field(default=None, max_length=20)
    horario: str | None = Field(default=None, max_length=100)

    @field_validator("horario")
    @classmethod
    def _horario_interpretable(cls, valor):
        return _validar_horario(valor)


class NegocioResponse(BaseModel):
    id_negocio: int
    id_administrador: int
    nombre_negocio: str
    direccion: str | None
    telefono: str | None
    horario: str | None
    estado_bot: bool

    model_config = {"from_attributes": True}


class EstadoBotResponse(BaseModel):
    id_negocio: int
    estado_bot: bool
