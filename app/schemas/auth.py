"""Schemas de entrada/salida para el módulo de autenticación (RF-01)."""
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class RegistroRequest(BaseModel):
    nombre: str = Field(min_length=2, max_length=100)
    correo: EmailStr
    contrasena: str = Field(min_length=8, max_length=72, description="Entre 8 y 72 caracteres")


class LoginRequest(BaseModel):
    correo: EmailStr
    contrasena: str


class RecuperarContrasenaRequest(BaseModel):
    correo: EmailStr


class AdministradorResponse(BaseModel):
    id_administrador: int
    nombre: str
    correo: EmailStr
    fecha_registro: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ActualizarCuentaRequest(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    correo: EmailStr | None = None
    contrasena_actual: str | None = Field(default=None, description="Obligatoria para cambiar el correo")


class CambiarContrasenaRequest(BaseModel):
    contrasena_actual: str
    nueva_contrasena: str = Field(min_length=8, max_length=72, description="Entre 8 y 72 caracteres")
