"""RF-01 | Autenticación: registro, login y recuperación de contraseña."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.security import hashear_contrasena, verificar_contrasena, crear_access_token
from app.core.deps import obtener_administrador_actual
from app.models.administrador import Administrador
from app.services.recuperacion import (
    enviar_correo_recuperacion,
    generar_token_recuperacion,
    verificar_token_recuperacion,
)
from app.schemas.auth import (
    RegistroRequest,
    LoginRequest,
    RecuperarContrasenaRequest,
    AdministradorResponse,
    TokenResponse,
)


router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/registro", response_model=AdministradorResponse, status_code=status.HTTP_201_CREATED)
def registrar_administrador(datos: RegistroRequest, db: Session = Depends(get_db)):
    ya_existe = db.query(Administrador).filter(Administrador.correo == datos.correo).first()
    if ya_existe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una cuenta registrada con ese correo",
        )

    nuevo_administrador = Administrador(
        nombre=datos.nombre,
        correo=datos.correo,
        contrasena=hashear_contrasena(datos.contrasena),
    )
    db.add(nuevo_administrador)
    db.commit()
    db.refresh(nuevo_administrador)

    return nuevo_administrador


@router.post("/login", response_model=TokenResponse)
def iniciar_sesion(datos: LoginRequest, db: Session = Depends(get_db)):
    credenciales_invalidas = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Correo o contraseña incorrectos",
    )

    administrador = db.query(Administrador).filter(Administrador.correo == datos.correo).first()
    if administrador is None:
        raise credenciales_invalidas

    if not verificar_contrasena(datos.contrasena, administrador.contrasena):
        raise credenciales_invalidas

    token = crear_access_token(administrador.id_administrador, administrador.correo)
    return TokenResponse(access_token=token)


@router.post("/recuperar-contrasena")
def recuperar_contrasena(datos: RecuperarContrasenaRequest, db: Session = Depends(get_db)):
    administrador = db.query(Administrador).filter(Administrador.correo == datos.correo).first()

    # Respuesta idéntica exista o no la cuenta: evita que alguien use este
    # endpoint para averiguar qué correos están registrados.
    if administrador:
        # TODO: generar un token temporal de un solo uso (ej. con expiración
        # de 15-30 min) y enviarlo por correo con un link a un formulario de
        # "nueva contraseña" en el Dashboard.
        pass

    return {"mensaje": "Si el correo está registrado, recibirás instrucciones para recuperar tu contraseña"}


@router.get("/yo", response_model=AdministradorResponse)
def obtener_perfil_propio(administrador_actual: Administrador = Depends(obtener_administrador_actual)):
    """Endpoint de ejemplo para probar que el token funciona: devuelve los
    datos del administrador dueño del token enviado."""
    return administrador_actual

class ForgotPasswordRequest(BaseModel):
    correo: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    nueva_contrasena: str = Field(min_length=8, max_length=128)


@router.post("/forgot-password")
def solicitar_recuperacion(
    datos: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    correo_norm = datos.correo.strip().lower()
    admin = db.query(Administrador).filter(Administrador.correo == correo_norm).first()

    if admin is not None:
        hash_actual = getattr(admin, "contraseña", None) or getattr(admin, "contrasena", "")
        token = generar_token_recuperacion(admin.correo, hash_actual)
        background_tasks.add_task(enviar_correo_recuperacion, admin.correo, admin.nombre, token)

    # Respuesta genérica para no revelar si el correo existe (RF-01)
    return {"detail": "Si el correo está registrado, recibirás un enlace para restablecer tu contraseña."}


@router.post("/reset-password")
def restablecer_contrasena(
    datos: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    payload = verificar_token_recuperacion(datos.token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace de recuperación es inválido o ya expiró.",
        )

    admin = db.query(Administrador).filter(Administrador.correo == payload["sub"]).first()
    if admin is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enlace inválido.")

    hash_actual = getattr(admin, "contraseña", None) or getattr(admin, "contrasena", "")
    if (hash_actual or "")[-12:] != payload.get("fp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este enlace ya fue utilizado.",
        )

    # Reemplaza `hashear_contrasena` por el helper bcrypt que ya usas en auth.py
    nuevo_hash = hashear_contrasena(datos.nueva_contrasena)
    if hasattr(admin, "contraseña"):
        admin.contraseña = nuevo_hash
    else:
        admin.contrasena = nuevo_hash

    db.commit()
    return {"detail": "Contraseña actualizada correctamente. Ya puedes iniciar sesión."}