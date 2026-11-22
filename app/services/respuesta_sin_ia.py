"""
Respuesta cuando la IA no está disponible (todos los modelos fallaron).

En vez de "no puedo procesar tu mensaje", el bot contesta con los datos
reales del negocio según lo que el cliente pregunta: servicios y precios,
horario, dirección o cómo agendar. Así el cliente siempre recibe algo útil,
y el negocio recibe un aviso en el Dashboard para seguir la conversación.
"""
import re
import unicodedata

_PALABRAS = {
    "servicios": ("servicio", "precio", "valor", "vale", "cuanto", "cuesta", "cobran", "catalogo",
                  "lista", "ofrecen", "hacen", "tienen", "corte", "tratamiento", "tarifa"),
    "horario": ("horario", "abren", "cierran", "atienden", "abierto", "hora de atencion", "que dias", "dias"),
    "direccion": ("direccion", "donde", "ubicacion", "ubicados", "queda", "llegar", "local", "mapa"),
    "agendar": ("agendar", "agenda", "reservar", "reserva", "cita", "turno", "disponible", "disponibilidad",
                "hora para", "una hora", "mañana", "manana", "hoy", "cancelar", "reagendar", "cambiar"),
}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes.lower())


def _detectar(texto: str) -> list[str]:
    t = _normalizar(texto)
    return [tema for tema, palabras in _PALABRAS.items() if any(p in t for p in palabras)]


def _precio(precio) -> str:
    try:
        return "$" + f"{int(round(float(precio))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def _lista_servicios(catalogo: list[dict]) -> str:
    lineas = []
    for s in catalogo[:15]:
        detalle = " · ".join(x for x in (_precio(s.get("precio")), f"{s['duracion_estimada']} min" if s.get("duracion_estimada") else "") if x)
        lineas.append(f"• *{s['nombre_servicio']}*" + (f" — {detalle}" if detalle else ""))
    return "\n".join(lineas)


def generar(
    mensaje_cliente: str,
    perfil_negocio: dict,
    catalogo: list[dict],
    catalogo_habilitado: bool = True,
    nombre_cliente: str | None = None,
) -> str:
    """Arma una respuesta con datos del negocio, sin llamar a la IA."""
    nombre_negocio = perfil_negocio.get("nombre_negocio") or "nuestro local"
    horario = perfil_negocio.get("horario")
    direccion = perfil_negocio.get("direccion")
    temas = _detectar(mensaje_cliente)
    mostrar_catalogo = catalogo_habilitado and bool(catalogo)

    partes: list[str] = []
    if "servicios" in temas and mostrar_catalogo:
        partes.append(f"Estos son nuestros servicios:\n{_lista_servicios(catalogo)}")
    if "horario" in temas and horario:
        partes.append(f"🕒 Atendemos: *{horario}*")
    if "direccion" in temas and direccion:
        partes.append(f"📍 Estamos en *{direccion}*")
    if "agendar" in temas:
        partes.append(
            "Para agendar, cuéntame qué servicio quieres, el día y la hora (con al menos 2 horas de "
            "anticipación), tu nombre, correo y un teléfono para los recordatorios. "
            "Alguien del equipo te confirma la hora a la brevedad."
        )

    saludo = f"¡Hola{' ' + nombre_cliente.split()[0] if nombre_cliente else ''}! 👋"
    if partes:
        return f"{saludo}\n\n" + "\n\n".join(partes) + "\n\n¿Te ayudo con algo más?"

    # No reconocimos la pregunta: presentamos lo principal del negocio
    resumen = [f"{saludo} Te escribe el asistente de *{nombre_negocio}*."]
    if mostrar_catalogo:
        resumen.append(f"Estos son nuestros servicios:\n{_lista_servicios(catalogo)}")
    datos = " · ".join(x for x in (f"🕒 {horario}" if horario else "", f"📍 {direccion}" if direccion else "") if x)
    if datos:
        resumen.append(datos)
    resumen.append("Cuéntame qué necesitas y alguien del equipo te responderá a la brevedad.")
    return "\n\n".join(resumen)
