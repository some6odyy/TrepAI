"""Envío de correos por SMTP (recordatorios y confirmaciones de citas).

Usa las mismas variables que la recuperación de contraseña:
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD. Si no están definidas,
el correo no se envía y solo queda en el log (modo demo).
"""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("trepai.email")


def smtp_configurado() -> bool:
    return bool(os.getenv("SMTP_USER", "").strip() and os.getenv("SMTP_PASSWORD", "").strip())


def enviar_correo(destino: str | None, asunto: str, texto: str, nombre_remitente: str = "TrepAI") -> bool:
    """Devuelve True si el correo salió. Nunca lanza excepción: un fallo de
    correo no debe tumbar el webhook ni el cron."""
    if not destino:
        return False
    if not smtp_configurado():
        logger.warning("[SIN SMTP] Correo a %s no enviado — %s\n%s", destino, asunto, texto)
        return False

    smtp_user = os.getenv("SMTP_USER", "").strip()
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = f"{nombre_remitente} <{smtp_user}>"
    msg["To"] = destino
    msg.set_content(texto)

    try:
        with smtplib.SMTP(
            os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587")), timeout=10
        ) as server:
            server.starttls()
            server.login(smtp_user, os.getenv("SMTP_PASSWORD", "").strip())
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo enviar el correo a %s: %s", destino, exc)
        return False
