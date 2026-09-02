"""
Conexión a la base de datos.
Usamos SQLAlchemy porque nos deja migrar de SQLite a Postgres/MySQL más
adelante sin reescribir los modelos ni las consultas.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import DATABASE_URL

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency de FastAPI: entrega una sesión y la cierra siempre al final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
