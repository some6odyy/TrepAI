"""Schemas de los horarios bloqueados (vacaciones, feriados, almuerzo)."""
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BloqueoCreate(BaseModel):
    tipo: Literal["fechas", "semanal"]
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    hora_desde: time | None = None
    hora_hasta: time | None = None
    dias_semana: list[int] = Field(default_factory=list, description="0 = lunes ... 6 = domingo (tipo semanal)")
    motivo: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validar(self):
        if (self.hora_desde is None) != (self.hora_hasta is None):
            raise ValueError("Indica la hora de inicio y la de término, o ninguna (día completo)")
        if self.hora_desde is not None and self.hora_hasta <= self.hora_desde:
            raise ValueError("La hora de término debe ser posterior a la de inicio")
        if self.tipo == "fechas":
            if self.fecha_desde is None:
                raise ValueError("Indica desde qué fecha")
            self.fecha_hasta = self.fecha_hasta or self.fecha_desde
            if self.fecha_hasta < self.fecha_desde:
                raise ValueError("La fecha de término no puede ser anterior a la de inicio")
            self.dias_semana = []
        else:
            dias = sorted({d for d in self.dias_semana if 0 <= d <= 6})
            if not dias:
                raise ValueError("Elige al menos un día de la semana")
            if self.hora_desde is None:
                raise ValueError("Un bloqueo semanal necesita hora de inicio y de término")
            self.dias_semana = dias
        return self


class BloqueoResponse(BaseModel):
    id_bloqueo: int
    tipo: str
    fecha_desde: date | None
    fecha_hasta: date | None
    hora_desde: time | None
    hora_hasta: time | None
    dias_semana: list[int]
    motivo: str | None
    descripcion: str
    # Citas activas que quedaron dentro del bloqueo (hay que moverlas a mano)
    citas_afectadas: int = 0
