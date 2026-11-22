"""
Capa de integración con el modelo de IA (Gemini/OpenAI).
RF-07 | Prompt dinámico: une el contexto del negocio con el mensaje.
RF-08 | Procesamiento NLP: llama al modelo y devuelve la respuesta.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from app.core.tiempo import ahora_chile  # noqa: F401 — se reexporta (agendado_ia/chat_service lo usan)
from app.core import config
from app.core.config import AI_PROVIDER, GEMINI_MODEL, OPENAI_MODEL, MAX_RESPONSE_LATENCY_SECONDS

logger = logging.getLogger("trepai.ai_service")

# Si la IA falla o se demora más de lo aceptable (RNF-01), el cliente
# igual necesita una respuesta — nunca dejamos el mensaje sin contestar.
RESPUESTA_DE_RESPALDO = (
    "Disculpa, en este momento no puedo procesar tu mensaje. "
    "Un miembro del equipo te va a responder apenas pueda."
)


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


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
    datos_cliente: dict | None = None,
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
            "Si el cliente quiere agendar, pide el servicio, el día y la hora (con al menos 2 horas "
            "de anticipación), además de su nombre completo, correo electrónico y un teléfono para "
            "enviarle recordatorios y la confirmación; luego dile que un miembro del equipo "
            "confirmará la disponibilidad."
        )

    dc = datos_cliente or {}
    datos_cliente_texto = (
        f"nombre: {dc.get('nombre') or '(no lo sabemos)'}; "
        f"correo: {dc.get('correo') or '(no lo sabemos)'}; "
        f"WhatsApp desde el que escribe: {dc.get('whatsapp') or '-'}"
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
        f"Datos guardados del cliente: {datos_cliente_texto}\n\n"
        f"Conversación hasta ahora:\n{historial_texto}\n\n"
        f"Nuevo mensaje del cliente:\n{mensaje_cliente}"
        
    )


class ErrorIA(Exception):
    """Falla de un intento contra el proveedor. reintentable=True para
    errores pasajeros (429 límite de uso, 5xx servidor saturado, red)."""

    def __init__(self, mensaje: str, reintentable: bool = True):
        super().__init__(mensaje)
        self.reintentable = reintentable


# Códigos HTTP que indican un problema pasajero del proveedor
_CODIGOS_PASAJEROS = {408, 429, 500, 502, 503, 504}
# No se empieza un intento si queda menos que esto del límite de RNF-01
_MINIMO_POR_INTENTO = 2.5


# Modelos "en pausa" tras una falla que no se arregla en segundos (cuota
# diaria agotada, modelo inexistente, clave inválida): no se vuelven a probar
# por un rato, así cada mensaje no pierde tiempo con ellos.
_pausados: dict[tuple[str, str], float] = {}


def _pausa_para(error: str) -> float:
    t = error.lower()
    if " 429" in t and ("perday" in t or "per day" in t):
        return 30 * 60
    if " 429" in t:
        return 60
    if any(f" {c}" in t for c in ("401", "403", "404")):
        return 10 * 60
    return 0


def _clave(provider: str) -> str:
    return config.GEMINI_API_KEY if provider == "gemini" else config.OPENAI_API_KEY


def _plan_de_intentos(provider: str | None, model: str | None) -> list[tuple[str, str]]:
    """Orden en que se prueban los modelos: primero el elegido por el negocio,
    luego los de respaldo del mismo proveedor y, si hay clave, los del otro."""
    provider = provider if provider in ("gemini", "openai") else AI_PROVIDER
    principal = model or (GEMINI_MODEL if provider == "gemini" else OPENAI_MODEL)
    otro = "openai" if provider == "gemini" else "gemini"
    respaldo = {"gemini": config.GEMINI_MODELOS_RESPALDO, "openai": config.OPENAI_MODELOS_RESPALDO}
    defecto = {"gemini": GEMINI_MODEL, "openai": OPENAI_MODEL}

    plan: list[tuple[str, str]] = []
    for prov, modelos in (
        (provider, [principal, *respaldo[provider]]),
        (otro, [defecto[otro], *respaldo[otro]]),
    ):
        if not _clave(prov):
            continue
        for m in modelos:
            if (prov, m) not in plan:
                plan.append((prov, m))
    return plan


def explicar_error(texto: str) -> str:
    """Traduce el error del proveedor a algo que el dueño entienda."""
    t = (texto or "").lower()
    if "timeout" in t:
        return "La IA se demoró demasiado en responder."
    if " 429" in t or "quota" in t or "resource_exhausted" in t or "rate limit" in t:
        if "per day" in t or "perday" in t or "daily" in t:
            return "Se agotó la cuota DIARIA gratuita de esta clave. Vuelve mañana o activa la facturación."
        return "Límite de uso alcanzado (cuota o demasiadas solicitudes por minuto)."
    if " 404" in t or "not found" in t:
        return "Ese modelo no existe o tu clave no tiene acceso a él."
    if "api key" in t or " 401" in t or " 403" in t or "permission" in t:
        return "La clave de IA no es válida o no tiene permisos."
    if " 400" in t:
        return "El proveedor rechazó la solicitud (400)."
    if any(f" {c}" in t for c in ("500", "502", "503", "504")) or "overloaded" in t or "unavailable" in t:
        return "El servidor de la IA está saturado o caído en este momento."
    if "vac" in t or "bloque" in t or "sin candidatos" in t:
        return "La IA devolvió una respuesta vacía o bloqueada."
    return "Error de conexión con la IA."


def generar_respuesta_o_none(
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
    registro: list | None = None,
) -> str | None:
    """Prueba el modelo del negocio y, si falla, los de respaldo, sin pasarse
    del límite de RNF-01 (MAX_RESPONSE_LATENCY_SECONDS en total).
    Devuelve el texto o None si ningún intento funcionó.
    Si se pasa `registro` (lista), se anota cada intento: lo usa el botón
    "Probar IA" del Dashboard para mostrar qué falla."""
    plan = _plan_de_intentos(provider, model)
    if not plan:
        logger.error("No hay clave de IA configurada (AI_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY)")
        if registro is not None:
            registro.append({"proveedor": "-", "modelo": "-", "ok": False,
                             "detalle": "No hay clave de IA configurada en el servidor (AI_API_KEY)."})
        return None

    def anotar(prov, modelo, ok, detalle, t0):
        if registro is not None:
            registro.append({"proveedor": prov, "modelo": modelo, "ok": ok,
                             "detalle": detalle, "segundos": round(time.monotonic() - t0, 1)})

    inicio = time.monotonic()

    def restante() -> float:
        return MAX_RESPONSE_LATENCY_SECONDS - (time.monotonic() - inicio)

    # Los modelos en pausa van al final (se prueban solo si los demás fallan)
    ahora = time.monotonic()
    plan = [x for x in plan if _pausados.get(x, 0) <= ahora] + [x for x in plan if _pausados.get(x, 0) > ahora]

    for i, (prov, modelo) in enumerate(plan):
        # Un error pasajero (429/503) merece un segundo intento con el mismo modelo
        for intento in (1, 2):
            if restante() < _MINIMO_POR_INTENTO:
                if registro is not None:
                    registro.append({"proveedor": prov, "modelo": modelo, "ok": False,
                                     "detalle": "No alcanzó a probarse: se acabaron los 15 s."})
                logger.error("Se acabó el tiempo de RNF-01 tras %s intento(s) de IA", i + intento - 1)
                return None
            t0 = time.monotonic()
            try:
                # Dejamos tiempo para un respaldo si este modelo se cuelga
                limite = restante() if i == len(plan) - 1 else min(restante(), max(restante() * 0.6, _MINIMO_POR_INTENTO))
                if prov == "gemini":
                    texto = _generar_respuesta_gemini(prompt, modelo, limite)
                else:
                    texto = _generar_respuesta_openai(prompt, modelo, limite)
                if not texto:
                    raise ErrorIA("respuesta vacía", reintentable=False)
                anotar(prov, modelo, True, "Respondió correctamente.", t0)
                _pausados.pop((prov, modelo), None)
                if i or intento > 1:
                    logger.warning("IA respondió con %s/%s tras fallas previas", prov, modelo)
                return texto
            except requests.Timeout:
                logger.warning("IA %s/%s: timeout", prov, modelo)
                anotar(prov, modelo, False, explicar_error("timeout"), t0)
                break  # pasar al siguiente modelo, no repetir uno lento
            except ErrorIA as error:
                logger.warning("IA %s/%s falló (intento %s): %s", prov, modelo, intento, error)
                anotar(prov, modelo, False, f"{explicar_error(str(error))} [{str(error)[:160]}]", t0)
                pausa = _pausa_para(str(error))
                if pausa:
                    _pausados[(prov, modelo)] = time.monotonic() + pausa
                if pausa > 60 or not error.reintentable or intento == 2:
                    break
                time.sleep(min(1.0, max(restante() - _MINIMO_POR_INTENTO, 0)))
            except (requests.RequestException, KeyError, IndexError, ValueError) as error:
                logger.warning("IA %s/%s error: %s", prov, modelo, error)
                anotar(prov, modelo, False, f"{explicar_error(str(error))} [{str(error)[:160]}]", t0)
                break
    return None


def generar_respuesta(prompt: str, provider: str | None = None, model: str | None = None) -> str:
    """Compatibilidad: igual que generar_respuesta_o_none pero, si todo falla,
    devuelve RESPUESTA_DE_RESPALDO. El chat usa la respuesta sin IA
    (respuesta_sin_ia.py), que es más útil para el cliente."""
    return generar_respuesta_o_none(prompt, provider, model) or RESPUESTA_DE_RESPALDO


def _revisar_estado(respuesta, proveedor: str, modelo: str) -> None:
    if respuesta.ok:
        return
    detalle = " ".join((respuesta.text or "")[:1500].split())
    raise ErrorIA(
        f"{proveedor} {modelo} respondió {respuesta.status_code}: {detalle}",
        reintentable=respuesta.status_code in _CODIGOS_PASAJEROS,
    )


def _generar_respuesta_gemini(prompt: str, modelo: str, limite: float = MAX_RESPONSE_LATENCY_SECONDS) -> str:
    """https://ai.google.dev/gemini-api/docs/text-generation

    Los modelos Gemini 3.x "piensan" antes de responder y con el nivel por
    defecto pueden pasar los 15 s de RNF-01. Para un chat de atención basta
    con pensamiento bajo ("low"), que responde mucho más rápido. Si el
    modelo no acepta ese parámetro (400), se reintenta sin él."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if modelo.startswith("gemini-3"):
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": "low"}}

    inicio = time.monotonic()
    respuesta = requests.post(url, headers=headers, json=body, timeout=limite)

    if respuesta.status_code == 400 and "generationConfig" in body:
        logger.warning("El modelo %s no aceptó thinkingLevel=low, reintentando sin él", modelo)
        body.pop("generationConfig")
        respuesta = requests.post(url, headers=headers, json=body, timeout=max(limite - (time.monotonic() - inicio), 1))

    _revisar_estado(respuesta, "gemini", modelo)
    data = respuesta.json()

    candidatos = data.get("candidates") or []
    if not candidatos:
        # Bloqueado por filtros de seguridad o sin candidatos
        motivo = (data.get("promptFeedback") or {}).get("blockReason", "sin candidatos")
        raise ErrorIA(f"gemini {modelo} no devolvió respuesta ({motivo})", reintentable=False)
    partes = (candidatos[0].get("content") or {}).get("parts") or []
    # Con thinking activo pueden venir partes de "pensamiento"; solo usamos el texto final
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    return texto.strip()


def _generar_respuesta_openai(prompt: str, modelo: str, limite: float = MAX_RESPONSE_LATENCY_SECONDS) -> str:
    """https://platform.openai.com/docs/api-reference/chat"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt}],
    }

    respuesta = requests.post(url, headers=headers, json=body, timeout=limite)
    _revisar_estado(respuesta, "openai", modelo)
    data = respuesta.json()

    return (data["choices"][0]["message"]["content"] or "").strip()
