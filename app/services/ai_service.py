"""
Capa de integración con el modelo de IA (Gemini/OpenAI).
RF-07 | Prompt dinámico: une el contexto del negocio con el mensaje.
RF-08 | Procesamiento NLP: llama al modelo y devuelve la respuesta.
"""
import logging

import requests

from app.core.config import AI_PROVIDER, AI_API_KEY, GEMINI_MODEL, OPENAI_MODEL, MAX_RESPONSE_LATENCY_SECONDS

logger = logging.getLogger("trepai.ai_service")

# Si la IA falla o se demora más de lo aceptable (RNF-01), el cliente
# igual necesita una respuesta — nunca dejamos el mensaje sin contestar.
RESPUESTA_DE_RESPALDO = (
    "Disculpa, en este momento no puedo procesar tu mensaje. "
    "Un miembro del equipo te va a responder apenas pueda."
)


def armar_prompt(reglas_negocio: str, instrucciones: str, catalogo: list, mensaje_cliente: str, catalogo_habilitado: bool = True) -> str:
    """Combina el contexto configurado por el administrador (RF-03) con el
    mensaje entrante del cliente final.

    Si catalogo_habilitado es False (el administrador apagó "Consultar
    catálogo" en el Dashboard), el prompt NO incluye precios ni servicios
    — el bot debe derivar esas consultas a una persona."""
    if not catalogo_habilitado:
        catalogo_texto = "(consulta de catálogo desactivada por el administrador — no menciones precios ni servicios, deriva esas preguntas a una persona del negocio)"
    else:
        catalogo_texto = "\n".join(
            f"- {s['nombre_servicio']}: ${s['precio']} ({s['duracion_estimada']} min)"
            for s in catalogo
        ) or "(el negocio aún no cargó su catálogo de servicios)"

    return (
        "Eres el asistente virtual de este negocio. Responde SIEMPRE en español, "
        "de forma breve, amable y natural, como si fueras parte del equipo. "
        "No inventes precios, horarios ni servicios que no estén en el contexto.\n\n"
        f"Reglas del negocio:\n{reglas_negocio or '(sin reglas adicionales)'}\n\n"
        f"Instrucciones del administrador:\n{instrucciones or '(sin instrucciones adicionales)'}\n\n"
        f"Catálogo de servicios:\n{catalogo_texto}\n\n"
        f"Mensaje del cliente:\n{mensaje_cliente}"
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
    """https://ai.google.dev/gemini-api/docs/text-generation"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent?key={AI_API_KEY}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    respuesta = requests.post(url, json=body, timeout=MAX_RESPONSE_LATENCY_SECONDS)
    respuesta.raise_for_status()
    data = respuesta.json()

    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


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
