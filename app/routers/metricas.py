"""RNF-01: tiempo de respuesta del bot (desde que llega el mensaje del
cliente hasta que sale la respuesta por WhatsApp)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import MAX_RESPONSE_LATENCY_SECONDS
from app.core.deps import obtener_negocio_propio
from app.core.tiempo import ahora_utc
from app.db.database import get_db
from app.models.conversacion import Conversacion, Mensaje
from app.models.negocio import Negocio

router = APIRouter(prefix="/negocio/{id_negocio}", tags=["Tiempo de respuesta"])


def _percentil(valores: list[int], p: float) -> int:
    ordenados = sorted(valores)
    indice = max(0, min(len(ordenados) - 1, round(p * (len(ordenados) - 1))))
    return ordenados[indice]


@router.get("/tiempo-respuesta")
def tiempo_respuesta(
    dias: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    negocio: Negocio = Depends(obtener_negocio_propio),
):
    """Promedio, percentil 95 y máximo de las respuestas automáticas del bot
    en los últimos `dias`, y qué porcentaje cumplió el límite de RNF-01."""
    latencias = [
        fila[0]
        for fila in db.query(Mensaje.latencia_ms)
        .join(Conversacion, Mensaje.id_conversacion == Conversacion.id_conversacion)
        .filter(
            Conversacion.id_negocio == negocio.id_negocio,
            Mensaje.emisor == "bot",
            Mensaje.latencia_ms.isnot(None),
            Mensaje.fecha_hora >= ahora_utc() - timedelta(days=dias),
        )
        .all()
    ]
    limite_ms = MAX_RESPONSE_LATENCY_SECONDS * 1000
    if not latencias:
        return {"dias": dias, "total": 0, "limite_s": MAX_RESPONSE_LATENCY_SECONDS}
    return {
        "dias": dias,
        "total": len(latencias),
        "limite_s": MAX_RESPONSE_LATENCY_SECONDS,
        "promedio_s": round(sum(latencias) / len(latencias) / 1000, 2),
        "p95_s": round(_percentil(latencias, 0.95) / 1000, 2),
        "maximo_s": round(max(latencias) / 1000, 2),
        "dentro_del_limite_pct": round(100 * sum(1 for l in latencias if l <= limite_ms) / len(latencias), 1),
    }
