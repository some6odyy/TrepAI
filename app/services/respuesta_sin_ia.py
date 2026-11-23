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


def detectar_temas(texto: str) -> list[str]:
    """Temas del negocio por los que pregunta el mensaje (servicios, horario, dirección, agendar)."""
    return _detectar(texto)


def es_saludo(texto: str) -> bool:
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    return bool(palabras) and palabras[0] in _SALUDOS


def es_agradecimiento(texto: str) -> bool:
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    return bool(palabras) and palabras[0] in _GRACIAS and len(palabras) <= 5


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


_RE_CUANDO = re.compile(
    r"\b(hoy|manana|pasado|lunes|martes|miercoles|jueves|viernes|sabado|domingo|a las|"
    r"\d{1,2}(:\d{2})?\s*(hrs?|h|am|pm)\b|\d{1,2}:\d{2})"
)


_SALUDOS = {"hola", "holi", "holis", "ola", "buenas", "buenos", "buen", "hey", "wena", "wenas", "alo", "hi", "hello"}
_GRACIAS = {"gracias", "grax", "muchas", "mil", "thanks", "vale", "perfecto", "genial", "ok", "okay", "listo", "dale"}


def _servicio_mencionado(texto: str, catalogo: list[dict]) -> dict | None:
    """El servicio del catálogo nombrado en el mensaje. Si calzan varios
    ("degradado" y "degradado con diseño"), gana el nombre más largo."""
    t = _normalizar(texto)
    candidatos = [s for s in catalogo if _normalizar(s.get("nombre_servicio", "")).strip() and
                  _normalizar(s["nombre_servicio"]).strip() in t]
    return max(candidatos, key=lambda s: len(s["nombre_servicio"])) if candidatos else None


def _detalle(servicio: dict) -> str:
    partes = [x for x in (_precio(servicio.get("precio")),
                          f"{servicio['duracion_estimada']} min" if servicio.get("duracion_estimada") else "") if x]
    return f" ({' · '.join(partes)})" if partes else ""


def generar(
    mensaje_cliente: str,
    perfil_negocio: dict,
    catalogo: list[dict],
    catalogo_habilitado: bool = True,
    nombre_cliente: str | None = None,
    ya_saludo: bool = False,
    catalogo_ya_enviado: bool = False,
) -> str:
    """Arma una respuesta con datos del negocio, sin llamar a la IA.
    ya_saludo / catalogo_ya_enviado evitan repetir el saludo y la lista
    completa de servicios en cada mensaje de la misma conversación."""
    nombre_negocio = perfil_negocio.get("nombre_negocio") or "nuestro local"
    horario = perfil_negocio.get("horario")
    direccion = perfil_negocio.get("direccion")
    temas = _detectar(mensaje_cliente)
    mostrar_catalogo = catalogo_habilitado and bool(catalogo)
    servicio = _servicio_mencionado(mensaje_cliente, catalogo) if mostrar_catalogo else None
    habla_de_cuando = bool(_RE_CUANDO.search(_normalizar(mensaje_cliente)))
    primer_nombre = nombre_cliente.split()[0] if nombre_cliente else ""
    saludo = "" if ya_saludo else f"¡Hola{' ' + primer_nombre if primer_nombre else ''}! 👋 "

    partes: list[str] = []

    if servicio is not None and ("agendar" in temas or habla_de_cuando):
        partes.append(
            f"¡Perfecto! Anoté *{servicio['nombre_servicio']}*{_detalle(servicio)} para el día y la hora que "
            f"indicaste. Para dejarla reservada envíame tu *nombre*, *correo* y un *teléfono* para los "
            f"recordatorios; alguien del equipo revisa la disponibilidad y te confirma a la brevedad."
        )
    elif servicio is not None:
        partes.append(
            f"*{servicio['nombre_servicio']}*{_detalle(servicio)}. ¿Para qué día y hora lo quieres? "
            f"(se agenda con al menos 2 horas de anticipación)"
        )
    else:
        if "servicios" in temas and mostrar_catalogo:
            partes.append(f"Estos son nuestros servicios:\n{_lista_servicios(catalogo)}")
        if "agendar" in temas:
            pide_servicio = "qué servicio quieres, " if mostrar_catalogo else ""
            partes.append(
                f"Para agendar, cuéntame {pide_servicio}el día y la hora (con al menos 2 horas de "
                "anticipación), tu nombre, correo y un teléfono para los recordatorios. "
                "Alguien del equipo te confirma la hora a la brevedad."
            )

    if "horario" in temas and horario:
        partes.append(f"🕒 Atendemos: *{horario}*")
    if "direccion" in temas and direccion:
        partes.append(f"📍 Estamos en *{direccion}*")

    if partes:
        return (saludo + "\n\n".join(partes)).strip()

    t = _normalizar(mensaje_cliente).strip(" !¡.?¿,")
    palabras = re.findall(r"[a-z]+", t)
    if palabras and palabras[0] in _GRACIAS and len(palabras) <= 5:
        return "¡De nada! 🙌 Cualquier otra cosa, aquí estoy."
    if ya_saludo or catalogo_ya_enviado:
        if palabras and (palabras[0] in _SALUDOS or t.startswith(("buenos dias", "buenas tardes", "buenas noches"))):
            return (
                f"¡Hola{' de nuevo, ' + primer_nombre if primer_nombre else ' de nuevo'}! 👋 ¿En qué te ayudo? "
                "Puedo contarte nuestros servicios y precios, el horario, la dirección o ayudarte a agendar una hora."
            )
        return (
            "No estoy seguro de haberte entendido 🙏 Puedo contarte nuestros *servicios y precios*, "
            "el *horario*, la *dirección* o ayudarte a *agendar*. Si prefieres, alguien del equipo te responde a la brevedad."
        )

    resumen = [f"{saludo}Te escribe el asistente de *{nombre_negocio}*.".strip()]
    if mostrar_catalogo:
        resumen.append(f"Estos son nuestros servicios:\n{_lista_servicios(catalogo)}")
    datos = " · ".join(x for x in (f"🕒 {horario}" if horario else "", f"📍 {direccion}" if direccion else "") if x)
    if datos:
        resumen.append(datos)
    resumen.append("¿Qué servicio te interesa?")
    return "\n\n".join(resumen)
