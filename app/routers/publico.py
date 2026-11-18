"""Páginas públicas (sin sesión) que abre el cliente final desde el correo.

GET /cita/respuesta?t=<token>&r=si|no -> confirma o cancela su cita. El token
va firmado con SECRET_KEY y vence 30 min después de la hora de la cita.
"""
from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.agenda import Agenda
from app.models.negocio import Negocio
from app.models.servicio import Servicio
from app.services import recordatorios

router = APIRouter(tags=["Público"])


def _pagina(titulo: str, texto: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(titulo)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#F6EEDF;color:#3A2A1C;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
main{{background:#fff;border-radius:14px;padding:32px;max-width:420px;box-shadow:0 6px 24px #0001;text-align:center}}
h1{{font-size:1.4rem;margin:0 0 12px}}p{{line-height:1.5;margin:0}}</style></head>
<body><main><h1>{escape(titulo)}</h1><p>{escape(texto)}</p></main></body></html>""")


@router.get("/cita/respuesta", response_class=HTMLResponse, include_in_schema=False)
def responder_cita(t: str = Query(...), r: str = Query(...), db: Session = Depends(get_db)):
    id_agenda = recordatorios.verificar_token_respuesta(t)
    if id_agenda is None or r not in ("si", "no"):
        return _pagina("Enlace no válido", "Este enlace venció o no es válido. Escríbenos por WhatsApp.")

    fila = (
        db.query(Agenda, Negocio)
        .join(Servicio, Agenda.id_servicio == Servicio.id_servicio)
        .join(Negocio, Servicio.id_negocio == Negocio.id_negocio)
        .filter(Agenda.id_agenda == id_agenda)
        .first()
    )
    if fila is None:
        return _pagina("Cita no encontrada", "No encontramos esta cita. Escríbenos por WhatsApp.")
    cita, negocio = fila

    if cita.respuesta_cliente is not None or cita.estado_cita not in recordatorios.ESTADOS_ACTIVOS:
        estado = "confirmada" if cita.estado_cita == "confirmada" else cita.estado_cita
        return _pagina("Ya registramos tu respuesta", f"Tu cita está {estado}. ¡Gracias!")

    texto = recordatorios.registrar_respuesta(db, negocio, cita, r).replace("*", "")
    return _pagina("¡Listo!" if r == "si" else "Cita cancelada", texto)
