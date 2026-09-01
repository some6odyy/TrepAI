"""
TrepAI - Backend
Plataforma SaaS que automatiza la atención al cliente de pymes vía
WhatsApp usando IA (ver informe TSI, Grupo 7).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.database import Base, engine
from app.core.config import CORS_ALLOWED_ORIGINS, BASE_DIR
from app import models  # noqa: F401 — registra los modelos en Base antes de crear las tablas
from app.routers import auth, negocio, contexto, webhook, agenda, conversaciones

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

app.include_router(auth.router)
app.include_router(negocio.router)
app.include_router(contexto.router)
app.include_router(agenda.router)
app.include_router(conversaciones.router)
app.include_router(webhook.router)


@app.get("/")
def estado_del_servicio():
    return {"servicio": "TrepAI", "estado": "operativo"}


# El Dashboard queda disponible en /dashboard/ — así en producción no se
# necesita un servidor web aparte solo para servir el frontend estático.
_frontend_dir = BASE_DIR / "frontend"
if _frontend_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_frontend_dir), html=True), name="dashboard")
