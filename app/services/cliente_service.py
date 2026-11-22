"""
El Cliente se identifica por su número de WhatsApp. Tanto la agenda manual
(el administrador crea una cita a mano) como el webhook de WhatsApp (RF-06)
necesitan la misma lógica: si el teléfono ya existe, se reutiliza; si no,
se crea.
"""
from sqlalchemy.orm import Session

from app.models.cliente import Cliente


def obtener_o_crear_cliente(
    db: Session,
    telefono: str,
    nombre: str | None = None,
    correo: str | None = None,
    reemplazar_nombre: bool = False,
) -> Cliente:
    """reemplazar_nombre=True cuando el nombre lo dio el propio cliente o el
    administrador al agendar (manda sobre el nombre de perfil de WhatsApp)."""
    cliente = db.query(Cliente).filter(Cliente.telefono == telefono).first()

    if cliente is None:
        cliente = Cliente(telefono=telefono, nombre_cliente=nombre, correo=correo)
        db.add(cliente)
        db.commit()
        db.refresh(cliente)
        return cliente

    cambios = False
    if nombre and (not cliente.nombre_cliente or (reemplazar_nombre and cliente.nombre_cliente != nombre)):
        # Si antes no teníamos su nombre y ahora sí, lo completamos.
        cliente.nombre_cliente = nombre
        cambios = True
    if correo and cliente.correo != correo:
        # El último correo que el cliente dio al agendar es el vigente.
        cliente.correo = correo
        cambios = True
    if cambios:
        db.commit()
        db.refresh(cliente)
    return cliente
