import base64
from email.message import EmailMessage
import hashlib
import hmac
import json
import logging
import os
import smtplib
import time

logger = logging.getLogger(__name__)

EXPIRACION = 1800 #30 minutos

def _secret_key() -> bytes:
    return os.getenv("SECRET_KEY", "cambia-esta-clave-por-una-aleatoria").encode("utf-8")

def generar_token_recuperacion(correo: str, hash_actual: str) -> str:
    payload = {
        "sub": correo.strip().lower(),
        "exp": int(time.time()) + EXPIRACION,
        "fp": (hash_actual or "")[-12:],  # invalida el token al cambiar la clave
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    firma = hmac.new(_secret_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{firma}"

def verificar_token_recuperacion(token: str) -> dict | None:
    """Valida firma y expiración. Devuelve el payload o None si no es válido."""
    try:
        raw, firma = token.split(".", 1)
        esperada = hmac.new(_secret_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, esperada):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None

def enviar_correo_recuperacion(correo: str, nombre: str, token: str) -> None:
    """Envía el enlace de restablecimiento por SMTP o lo imprime en consola si no hay SMTP."""
    app_url = os.getenv("APP_URL", "http://127.0.0.1:8000").rstrip("/")
    enlace = f"{app_url}/dashboard/?reset_token={token}"

    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    if not smtp_user or not smtp_pass:
        logger.warning("🔑 [MODO DEMO - SIN SMTP] Enlace de recuperación para %s:\n%s", correo, enlace)
        print(f"\n🔑 Enlace de recuperación para {correo}:\n{enlace}\n")
        return

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    msg = EmailMessage()
    msg["Subject"] = "Restablecer contraseña — TrepAI"
    msg["From"] = f"TrepAI <{smtp_user}>"
    msg["To"] = correo
    msg.set_content(
        f"Hola {nombre or 'administrador'},\n\n"
        f"Recibimos una solicitud para restablecer tu contraseña en TrepAI.\n"
        f"Abre el siguiente enlace (válido por 30 minutos):\n\n{enlace}\n\n"
        f"Si no solicitaste este cambio, ignora este correo."
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)