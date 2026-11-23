"""Configuración común para correr todas las pruebas juntas con pytest:

    pytest tests/

- Base de datos SQLite temporal propia de la sesión de pruebas.
- Cada prueba reemplaza funciones a mano (ej. `ai_service.ahora_chile =
  lambda: ...`). Para que eso no se filtre a la prueba siguiente, después
  de cada prueba se restauran los módulos de `app` como estaban.
"""
import os
import sys
import tempfile
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.gettempdir()}/trepai_pruebas_{uuid.uuid4().hex}.db")
os.environ.setdefault("AI_API_KEY", "clave-falsa")
# Las pruebas no deben depender de la configuración local del desarrollador
os.environ["META_APP_SECRET"] = ""


def _modulos_app():
    return {n: m for n, m in list(sys.modules.items()) if n == "app" or n.startswith("app.")}


def _copiar(valor):
    if isinstance(valor, (dict, list, set)):
        return type(valor)(valor)
    return valor


@pytest.fixture(autouse=True)
def restaurar_modulos():
    antes = {
        nombre: {k: _copiar(v) for k, v in vars(m).items() if not k.startswith("__")}
        for nombre, m in _modulos_app().items()
    }
    yield
    for nombre, modulo in _modulos_app().items():
        guardado = antes.get(nombre)
        if guardado is None:
            continue
        actual = vars(modulo)
        for clave, valor in guardado.items():
            if isinstance(valor, (dict, list, set)) and isinstance(actual.get(clave), type(valor)):
                actual[clave].clear()
                actual[clave].update(valor) if not isinstance(valor, list) else actual[clave].extend(valor)
            else:
                actual[clave] = valor


@pytest.fixture(autouse=True, scope="module")
def base_de_datos_propia():
    """Cada archivo de pruebas trabaja sobre una base SQLite nueva, para que
    las citas o mensajes de un archivo no aparezcan en otro."""
    from sqlalchemy import create_engine

    from app.db import database

    url = f"sqlite:///{tempfile.gettempdir()}/trepai_pruebas_{uuid.uuid4().hex}.db"
    motor = create_engine(url)
    database.Base.metadata.create_all(bind=motor)
    database.SessionLocal.configure(bind=motor)
    yield
    motor.dispose()
