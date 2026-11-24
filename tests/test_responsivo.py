"""Diseño responsivo del Dashboard (RNF-03): que se entreguen los recursos
del menú para celular y que las respuestas grandes viajen comprimidas.
    PYTHONPATH=. python -m pytest tests/test_responsivo.py

La verificación visual (sin desplazamiento horizontal en 320, 360, 390, 768,
1024 y 1366 px) se hace con el navegador; ver la sección de pruebas del informe.
"""
import os, tempfile, uuid

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.gettempdir()}/trepai_test_{uuid.uuid4().hex}.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)


def test_dashboard_trae_menu_para_celular():
    r = c.get("/dashboard/")
    assert r.status_code == 200
    html = r.text
    assert 'name="viewport" content="width=device-width, initial-scale=1.0"' in html
    assert 'href="css/responsivo.css"' in html
    # Botón ☰ que abre el menú lateral en pantallas angostas
    assert 'id="btnMenu"' in html and 'aria-controls="sidebar"' in html
    assert 'id="sidebar"' in html and 'id="menuBackdrop"' in html
    # Las 7 secciones son botones (se pueden usar con el teclado)
    for vista in ("resumen", "contexto", "agenda", "historial", "clientes", "casos", "cuenta"):
        assert f'class="nav-item' in html and f'data-view="{vista}"' in html
    assert html.count('<button type="button" class="nav-item') == 7
    # Las fuentes ya no se cargan con @import (bloqueaba el primer pintado)
    css = c.get("/dashboard/css/styles.css").text
    assert "@import url(" not in css


def test_hoja_responsiva_cubre_los_anchos_del_rnf03():
    css = c.get("/dashboard/css/responsivo.css")
    assert css.status_code == 200
    for quiebre in ("max-width: 1100px", "max-width: 900px", "max-width: 600px", "max-width: 380px"):
        assert quiebre in css.text


def test_respuestas_grandes_van_comprimidas():
    r = c.get("/dashboard/js/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    # Las respuestas chicas de la API no se comprimen
    assert c.get("/status", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") is None
