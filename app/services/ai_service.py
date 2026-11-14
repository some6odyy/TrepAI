"""
Capa de integración con el modelo de IA (Gemini/OpenAI).
RF-07 | Prompt dinámico: une el contexto del negocio con el mensaje.
RF-08 | Procesamiento NLP: llama al modelo y devuelve la respuesta.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from app.core.config import AI_PROVIDER, AI_API_KEY, GEMINI_MODEL, OPENAI_MODEL, MAX_RESPONSE_LATENCY_SECONDS

logger = logging.getLogger("trepai.ai_service")

# Si la IA falla o se demora más de lo aceptable (RNF-01), el cliente
# igual necesita una respuesta — nunca dejamos el mensaje sin contestar.
RESPUESTA_DE_RESPALDO = (
    "Disculpa, en este momento no puedo procesar tu mensaje. "
    "Un miembro del equipo te va a responder apenas pueda."
)


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def ahora_chile() -> datetime:
    """Hora local de Chile para que el bot entienda "mañana", "el martes", etc.
    En Windows zoneinfo puede no tener la base de zonas horarias (falta el
    paquete tzdata); en ese caso usamos UTC-3 fijo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Santiago"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-3)))


def _formatear_precio(precio) -> str:
    """6700 -> "$6.700" (formato chileno, sin decimales)."""
    try:
        return "$" + f"{int(round(float(precio))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return f"${precio}"


def armar_prompt(
    reglas_negocio: str,
    instrucciones: str,
    catalogo: list,
    mensaje_cliente: str,
    catalogo_habilitado: bool = True,
    historial: list | None = None,
    perfil_negocio: dict | None = None,
    agendar_automatico: bool = False,
    cita_activa: str | None = None,
) -> str:
    """Combina el contexto configurado por el administrador (RF-03) con el
    mensaje entrante del cliente final.

    historial: mensajes anteriores de la misma conversación, como lista de
    (emisor, contenido), del más antiguo al más reciente. Le da "memoria" al
    bot para que siga el hilo (ej. "el martes a las 14:00" después de
    preguntar por un degradado) y no salude de nuevo en cada mensaje.

    Si catalogo_habilitado es False (el administrador apagó "Consultar
    catálogo" en el Dashboard), el prompt NO incluye precios ni servicios
    — el bot debe derivar esas consultas a una persona."""
    if not catalogo_habilitado:
        catalogo_texto = "(consulta de catálogo desactivada por el administrador — no menciones precios ni servicios, deriva esas preguntas a una persona del negocio)"
    else:
        catalogo_texto = "\n".join(
            f"- {s['nombre_servicio']}: {_formatear_precio(s['precio'])} ({s['duracion_estimada']} min)"
            for s in catalogo
        ) or "(el negocio aún no cargó su catálogo de servicios)"

    perfil = perfil_negocio or {}
    datos_negocio = "\n".join(
        f"- {etiqueta}: {perfil[clave]}"
        for clave, etiqueta in (
            ("nombre_negocio", "Nombre"),
            ("direccion", "Dirección"),
            ("telefono", "Teléfono"),
            ("horario", "Horario de atención"),
        )
        if perfil.get(clave)
    ) or "(sin datos del negocio)"

    if agendar_automatico:
        from app.services.agendado_ia import instrucciones_para_prompt
        instruccion_agenda = instrucciones_para_prompt()
    else:
        instruccion_agenda = (
            "Si el cliente quiere agendar, pide el servicio, el día y la hora, y dile que "
            "un miembro del equipo confirmará la disponibilidad."
        )

    ahora = ahora_chile()
    fecha_actual = f"{_DIAS[ahora.weekday()]} {ahora.strftime('%d-%m-%Y %H:%M')}"

    if historial:
        historial_texto = "\n".join(
            f"{'Cliente' if emisor == 'cliente' else 'Asistente'}: {contenido}"
            for emisor, contenido in historial
        )
        indicacion_saludo = "La conversación ya empezó: NO vuelvas a saludar ni a presentarte, continúa el hilo."
    else:
        historial_texto = "(es el primer mensaje de esta conversación)"
        indicacion_saludo = "Es el primer mensaje: saluda brevemente."

    return (
        "Eres el asistente virtual de este negocio y atiendes a sus clientes por WhatsApp. "
        "Responde SIEMPRE en español, de forma breve (máximo 3-4 líneas), amable y natural, "
        "como si fueras parte del equipo. "
        "No inventes precios, horarios ni servicios que no estén en el contexto. "
        f"{instruccion_agenda}\n"
        "Formato de WhatsApp: para negrita usa UN asterisco (*texto*), nunca dos; "
        "no uses títulos con # ni tablas. Escribe los precios en formato chileno (ej. $6.700).\n"
        f"{indicacion_saludo}\n\n"
        f"Fecha y hora actual (Chile): {fecha_actual}\n\n"
        f"Datos del negocio:\n{datos_negocio}\n\n"
        f"Reglas del negocio:\n{reglas_negocio or '(sin reglas adicionales)'}\n\n"
        f"Instrucciones del administrador:\n{instrucciones or '(sin instrucciones adicionales)'}\n\n"
        f"Catálogo de servicios:\n{catalogo_texto}\n\n"
        f"Cita activa actual del cliente: {cita_activa or 'ninguna'}\n\n"
        f"Conversación hasta ahora:\n{historial_texto}\n\n"
        f"Nuevo mensaje del cliente:\n{mensaje_cliente}"
        
    )


def generar_respuesta(prompt: str, provider: str | None = None, model: str | None = None) -> str:
    """Envía el prompt al proveedor de IA y devuelve el texto de respuesta.

    provider/model son el motor elegido por ESE negocio en el bloque
    "Motor de IA" del Dashboard (RF-08); si no se pasan, se usa el
    default global del .env. Debe cumplir RNF-01 (ciclo total < 15
    segundos): si la IA no responde a tiempo o falla, usamos
    RESPUESTA_DE_RESPALDO en vez de dejar al cliente sin respuesta."""
    provider = provider or AI_PROVIDER

    if not AI_API_KEY:
        logger.warning("AI_API_KEY no configurada — no se puede consultar al proveedor de IA")
        return RESPUESTA_DE_RESPALDO

    try:
        if provider == "gemini":
            return _generar_respuesta_gemini(prompt, model or GEMINI_MODEL)
        elif provider == "openai":
            return _generar_respuesta_openai(prompt, model or OPENAI_MODEL)
        else:
            logger.error("Proveedor de IA desconocido: %s", provider)
            return RESPUESTA_DE_RESPALDO

    except requests.Timeout:
        logger.error("Timeout consultando al proveedor de IA (RNF-01: máx %ss)", MAX_RESPONSE_LATENCY_SECONDS)
        return RESPUESTA_DE_RESPALDO
    except (requests.RequestException, KeyError, IndexError) as error:
        logger.error("Error consultando al proveedor de IA: %s", error)
        return RESPUESTA_DE_RESPALDO


def _generar_respuesta_gemini(prompt: str, modelo: str) -> str:
    """https://ai.google.dev/gemini-api/docs/text-generation

    Los modelos Gemini 3.x "piensan" antes de responder y con el nivel por
    defecto pueden pasar los 15 s de RNF-01. Para un chat de atención basta
    con pensamiento bajo ("low"), que responde mucho más rápido. Si el
    modelo no acepta ese parámetro (400), se reintenta sin él."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent?key={AI_API_KEY}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if modelo.startswith("gemini-3"):
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": "low"}}

    inicio = time.monotonic()

    def restante() -> float:
        return MAX_RESPONSE_LATENCY_SECONDS - (time.monotonic() - inicio)

    respuesta = requests.post(url, json=body, timeout=restante())

    if respuesta.status_code == 400 and "generationConfig" in body:
        logger.warning("El modelo %s no aceptó thinkingLevel=low, reintentando sin él", modelo)
        body.pop("generationConfig")
        respuesta = requests.post(url, json=body, timeout=max(restante(), 1))

    # 429 = límite de solicitudes por minuto (frecuente en el plan gratuito):
    # un reintento corto si todavía queda tiempo dentro de RNF-01.
    if respuesta.status_code == 429 and restante() > 5:
        logger.warning("Gemini respondió 429 (límite de uso), reintentando en 2 s")
        time.sleep(2)
        respuesta = requests.post(url, json=body, timeout=max(restante(), 1))

    respuesta.raise_for_status()
    data = respuesta.json()

    partes = data["candidates"][0]["content"]["parts"]
    # Con thinking activo pueden venir partes de "pensamiento"; solo usamos el texto final
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    return texto.strip()


def _generar_respuesta_openai(prompt: str, modelo: str) -> str:
    """https://platform.openai.com/docs/api-reference/chat"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt}],
    }

    respuesta = requests.post(url, headers=headers, json=body, timeout=MAX_RESPONSE_LATENCY_SECONDS)
    respuesta.raise_for_status()
    data = respuesta.json()

    return data["choices"][0]["message"]["content"].strip()
