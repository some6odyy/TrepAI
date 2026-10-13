"""
Deja la base de datos de TrepAI en blanco: borra TODAS las cuentas de
administrador, sus negocios, los números de WhatsApp vinculados y todo lo
que cuelga de ellos (servicios, contexto IA, conversaciones, mensajes,
citas y clientes).

Las tablas quedan creadas (no se hace DROP), solo se vacían.

ES IRREVERSIBLE. Primero muestra cuántos registros hay y pide escribir
BORRAR para confirmar.

Uso (desde la raíz del proyecto, con el venv activado):

    # Usa el DATABASE_URL del .env (o SQLite local si no hay)
    python -m scripts.limpiar_bd

    # Apuntando explícitamente a la BD de producción (Neon):
    #   PowerShell:
    $env:DATABASE_URL="postgresql+psycopg://...neon.tech/...?sslmode=require"; python -m scripts.limpiar_bd
    #   bash:
    DATABASE_URL="postgresql+psycopg://..." python -m scripts.limpiar_bd

    # Sin pregunta de confirmación (ej. en CI):
    python -m scripts.limpiar_bd --si
"""
import sys

from app.db.database import SessionLocal, engine
from app.models import (
    Administrador,
    Agenda,
    Cliente,
    ContextoIA,
    Conversacion,
    Mensaje,
    Negocio,
    Servicio,
)

# Orden pensado para respetar las llaves foráneas: primero los hijos,
# al final los padres.
TABLAS_EN_ORDEN = [
    ("mensajes", Mensaje),
    ("conversaciones", Conversacion),
    ("citas (agenda)", Agenda),
    ("servicios", Servicio),
    ("contextos IA", ContextoIA),
    ("negocios (y sus números de WhatsApp)", Negocio),
    ("clientes", Cliente),
    ("cuentas de administrador", Administrador),
]


def _ocultar_clave(url: str) -> str:
    """Muestra a qué BD apunta sin imprimir la contraseña."""
    try:
        return engine.url.render_as_string(hide_password=True)
    except Exception:  # pragma: no cover
        return url.split("@")[-1]


def main() -> None:
    sin_preguntar = "--si" in sys.argv

    db = SessionLocal()
    try:
        print(f"Base de datos: {_ocultar_clave(str(engine.url))}\n")

        conteos = [(nombre, modelo, db.query(modelo).count()) for nombre, modelo in TABLAS_EN_ORDEN]
        numeros = db.query(Negocio).filter(Negocio.whatsapp_phone_number_id.isnot(None)).count()

        for nombre, _, total in reversed(conteos):
            print(f"  {total:>6}  {nombre}")
        print(f"  {numeros:>6}  de ellos con un número de WhatsApp vinculado\n")

        if sum(total for _, _, total in conteos) == 0:
            print("La base ya está vacía. Nada que hacer.")
            return

        if not sin_preguntar:
            respuesta = input("Esto borra TODO lo anterior y no se puede deshacer. Escribe BORRAR para continuar: ")
            if respuesta.strip() != "BORRAR":
                print("Cancelado. No se borró nada.")
                return

        for nombre, modelo, _ in conteos:
            db.query(modelo).delete(synchronize_session=False)
        db.commit()  # una sola transacción: o se borra todo, o nada

        print("\n✅ Listo. Base de datos vacía:")
        for nombre, modelo in TABLAS_EN_ORDEN:
            print(f"  {db.query(modelo).count():>6}  {nombre}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
