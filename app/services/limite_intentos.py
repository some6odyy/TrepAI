"""
Límite de intentos para el inicio de sesión y la recuperación de contraseña.

Se guarda en la base de datos (no en memoria) porque en Vercel cada
solicitud puede caer en una instancia distinta.

- Login: tras LOGIN_MAX_POR_CORREO intentos fallidos con el mismo correo (o
  LOGIN_MAX_POR_IP desde la misma IP) en VENTANA_LOGIN_MIN minutos, se
  bloquea hasta que pase la ventana. Un login correcto limpia el contador
  del correo.
- Recuperación: como máximo RECUP_MAX_POR_CORREO correos por hora a una
  misma cuenta (los extra se descartan en silencio, la respuesta es la misma)
  y RECUP_MAX_POR_IP solicitudes por hora desde una IP.
"""
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.tiempo import ahora_utc
from app.models.seguridad import IntentoAcceso

VENTANA_LOGIN_MIN = 15
LOGIN_MAX_POR_CORREO = 5
LOGIN_MAX_POR_IP = 20
VENTANA_RECUP_MIN = 60
RECUP_MAX_POR_CORREO = 3
RECUP_MAX_POR_IP = 10


def ip_de(request: Request) -> str:
    """IP real del cliente (Vercel la pone en X-Forwarded-For)."""
    reenviada = request.headers.get("x-forwarded-for", "")
    if reenviada:
        return reenviada.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "desconocida")[:60]


def _desde(minutos: int):
    return ahora_utc() - timedelta(minutes=minutos)


def contar(db: Session, tipo: str, clave: str, minutos: int) -> int:
    return (
        db.query(func.count(IntentoAcceso.id_intento))
        .filter(IntentoAcceso.tipo == tipo, IntentoAcceso.clave == clave, IntentoAcceso.fecha >= _desde(minutos))
        .scalar()
    ) or 0


def _minutos_restantes(db: Session, tipo: str, clave: str, minutos: int) -> int:
    primero = (
        db.query(func.min(IntentoAcceso.fecha))
        .filter(IntentoAcceso.tipo == tipo, IntentoAcceso.clave == clave, IntentoAcceso.fecha >= _desde(minutos))
        .scalar()
    )
    if primero is None:
        return 1
    restante = (primero + timedelta(minutes=minutos)) - ahora_utc()
    return max(1, int(restante.total_seconds() // 60) + 1)


def registrar(db: Session, tipo: str, *claves: str) -> None:
    for clave in claves:
        db.add(IntentoAcceso(tipo=tipo, clave=clave[:150]))
    db.commit()


def limpiar(db: Session, tipo: str, clave: str) -> None:
    db.query(IntentoAcceso).filter(IntentoAcceso.tipo == tipo, IntentoAcceso.clave == clave).delete()
    db.commit()


def limpiar_antiguos(db: Session) -> int:
    borrados = db.query(IntentoAcceso).filter(IntentoAcceso.fecha < ahora_utc() - timedelta(days=1)).delete()
    db.commit()
    return borrados


def _demasiados(minutos: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Demasiados intentos. Espera {minutos} minuto{'s' if minutos != 1 else ''} y vuelve a intentarlo.",
        headers={"Retry-After": str(minutos * 60)},
    )


def clave_correo(correo: str) -> str:
    return "correo:" + (correo or "").strip().lower()


def clave_ip(ip: str) -> str:
    return "ip:" + ip


def verificar_login(db: Session, correo: str, ip: str) -> None:
    """Lanza 429 si el correo o la IP superaron el límite de intentos fallidos."""
    for clave, maximo in ((clave_correo(correo), LOGIN_MAX_POR_CORREO), (clave_ip(ip), LOGIN_MAX_POR_IP)):
        if contar(db, "login", clave, VENTANA_LOGIN_MIN) >= maximo:
            raise _demasiados(_minutos_restantes(db, "login", clave, VENTANA_LOGIN_MIN))


def verificar_recuperacion(db: Session, correo: str, ip: str) -> bool:
    """Lanza 429 si la IP pidió demasiados enlaces. Devuelve False si a ese
    correo ya se le mandaron demasiados (se responde igual, sin enviar)."""
    if contar(db, "recuperacion", clave_ip(ip), VENTANA_RECUP_MIN) >= RECUP_MAX_POR_IP:
        raise _demasiados(_minutos_restantes(db, "recuperacion", clave_ip(ip), VENTANA_RECUP_MIN))
    registrar(db, "recuperacion", clave_ip(ip))
    if contar(db, "recuperacion", clave_correo(correo), VENTANA_RECUP_MIN) >= RECUP_MAX_POR_CORREO:
        return False
    registrar(db, "recuperacion", clave_correo(correo))
    return True
