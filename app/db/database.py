"""
Conexión a la base de datos.
Usamos SQLAlchemy porque nos deja migrar de SQLite a Postgres/MySQL más
adelante sin reescribir los modelos ni las consultas.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from app.core.config import DATABASE_URL

engine_kwargs = {"pool_pre_ping": True}
if not DATABASE_URL.startswith("sqlite"):
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency de FastAPI: entrega una sesión y la cierra siempre al final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
