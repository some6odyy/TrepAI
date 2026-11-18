"""
TrepAI - Backend
Plataforma SaaS que automatiza la atención al cliente de pymes vía
WhatsApp usando IA (ver informe TSI, Grupo 7).
"""
from fastapi import FastAPI
from sqlalchemy import inspect, text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.db.database import Base, engine
from app.core.config import CORS_ALLOWED_ORIGINS, BASE_DIR
from app import models  # noqa: F401 — registra los modelos en Base antes de crear las tablas
from app.routers import auth, negocio, contexto, webhook, agenda, conversaciones, puente, tareas, publico, novedades

app = FastAPI(
    title="TrepAI",
    description="Chatbot con IA para pymes y microempresas vía WhatsApp",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,  # usamos Bearer token, no cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# Crea las tablas si no existen (para desarrollo local con SQLite).
# En producción esto se reemplaza por migraciones con Alembic.
Base.metadata.create_all(bind=engine)


# create_all NO agrega columnas a tablas que ya existen (ej. la BD de Neon en
# producción). Aquí se añaden las columnas nuevas si faltan.
_COLUMNAS_NUEVAS = [
    ("agenda", "fecha_creacion", "TIMESTAMP"),
    ("agenda", "nombre_contacto", "VARCHAR(100)"),
    ("agenda", "correo_contacto", "VARCHAR(100)"),
    ("agenda", "telefono_contacto", "VARCHAR(20)"),
    ("agenda", "aviso_reserva_enviado", "TIMESTAMP"),
    ("agenda", "recordatorio_24h_enviado", "TIMESTAMP"),
    ("agenda", "confirmacion_1h_enviada", "TIMESTAMP"),
    ("agenda", "respuesta_cliente", "VARCHAR(10)"),
    ("cliente", "correo", "VARCHAR(100)"),
]


def _asegurar_columnas_nuevas():
    """Las citas antiguas quedan con fecha_creacion = ahora, así no vencen
    de golpe. A las citas que ya existían no se les manda el aviso "al
    agendar" (ya fueron agendadas antes); sí reciben los recordatorios de
    24 h y 1 h si todavía están por venir."""
    inspector = inspect(engine)
    for tabla, columna, tipo in _COLUMNAS_NUEVAS:
        existentes = {c["name"] for c in inspector.get_columns(tabla)}
        if columna in existentes:
            continue
        with engine.begin() as conexion:
            conexion.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))
            if (tabla, columna) == ("agenda", "fecha_creacion"):
                conexion.execute(text("UPDATE agenda SET fecha_creacion = CURRENT_TIMESTAMP"))
            if (tabla, columna) == ("agenda", "aviso_reserva_enviado"):
                conexion.execute(text("UPDATE agenda SET aviso_reserva_enviado = CURRENT_TIMESTAMP"))


_asegurar_columnas_nuevas()

app.include_router(auth.router)
app.include_router(negocio.router)
app.include_router(contexto.router)
app.include_router(agenda.router)
app.include_router(conversaciones.router)
app.include_router(webhook.router)
app.include_router(novedades.router)
app.include_router(tareas.router)
app.include_router(publico.router)
app.include_router(puente.router)  # demo con whatsapp-web.js (desactivado sin PUENTE_TOKEN)


@app.get("/")
def raiz():
    return RedirectResponse(url="/dashboard/")


@app.get("/status")
def estado_del_servicio():
    return {"servicio": "TrepAI", "estado": "operativo"}


# El Dashboard queda disponible en /dashboard/ — así en producción no se
# necesita un servidor web aparte solo para servir el frontend estático.
_frontend_dir = BASE_DIR / "frontend"
if _frontend_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_frontend_dir), html=True), name="dashboard")
