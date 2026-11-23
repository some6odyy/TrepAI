"""Tareas programadas: recordatorios y confirmaciones de citas.

Vercel (plan Hobby) solo permite crons diarios, así que un cron externo
llama a este endpoint cada 5 minutos — por defecto el workflow de GitHub
Actions `.github/workflows/recordatorios.yml`. Sirve igual con cron-job.org
o con Vercel Cron en un plan pagado.

Autenticación: header `Authorization: Bearer <CRON_SECRET>` (formato que
usa Vercel Cron) o `X-Cron-Token: <CRON_SECRET>`. Sin CRON_SECRET definido,
el endpoint está desactivado.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core import config
from app.db.database import get_db
from app.services import recordatorios

router = APIRouter(prefix="/tareas", tags=["Tareas programadas"])


def _verificar_cron(authorization: str = Header(default=""), x_cron_token: str = Header(default="")):
    if not config.CRON_SECRET:
        raise HTTPException(status_code=404, detail="Tareas desactivadas (define CRON_SECRET)")
    enviado = x_cron_token or authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(enviado, config.CRON_SECRET):
        raise HTTPException(status_code=401, detail="Token de cron inválido")


@router.api_route("/recordatorios", methods=["GET", "POST"], dependencies=[Depends(_verificar_cron)])
def ejecutar_recordatorios(db: Session = Depends(get_db)):
    resultado = recordatorios.ejecutar_pendientes(db)
    # Limpieza: IDs de mensajes de WhatsApp e intentos de acceso antiguos
    from app.services import chat_service, limite_intentos
    chat_service.limpiar_eventos_antiguos(db)
    limite_intentos.limpiar_antiguos(db)
    return {"ok": True, **resultado}
