"""Recepcionista (Haiku) + Director con herramientas reales (Sonnet)."""
import asyncio
import json
import logging
import time

from anthropic import AsyncAnthropic

from comunicaciones import responder_por_telegram
from config import Config
from herramientas import DEFINICIONES, ejecutar_herramienta
from notificaciones import enviar_alerta_ntfy

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=Config.ANTHROPIC_API_KEY or None)

RAMAS = ["codigo", "finanzas", "investigacion", "sistemas"]
MAX_HISTORIAL = 20          # mensajes que recuerda el recepcionista
MAX_ITERACIONES = 15        # vueltas máximas del bucle del Director
_directores = asyncio.Semaphore(2)

# Referencias fuertes a las tareas en segundo plano (si no, el GC puede eliminarlas).
_tareas: dict[asyncio.Task, dict] = {}
_historial: list[dict] = []


class RechazoModelo(Exception):
    pass


# =====================================================================
# Historial de conversación (texto plano, solo se añade al final)
# =====================================================================
def _anotar(rol: str, texto: str):
    if _historial and _historial[-1]["role"] == rol:
        _historial[-1]["content"] += "\n\n" + texto
    else:
        _historial.append({"role": rol, "content": texto})
    del _historial[:-MAX_HISTORIAL]
    while _historial and _historial[0]["role"] != "user":
        _historial.pop(0)


def tareas_activas() -> list[str]:
    ahora = time.monotonic()
    return [f"[{t['rama']}] {t['instruccion'][:80]} ({int(ahora - t['inicio'])}s)" for t in _tareas.values()]


# =====================================================================
# Director de rama: bucle de agente con herramientas reales
# =====================================================================
HERRAMIENTA_RESOLUCION = {
    "name": "emitir_resolucion",
    "description": "Entrega el resultado final de la tarea. Llámala una sola vez, al terminar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "es_critico": {
                "type": "boolean",
                "description": "True solo si hay un fallo grave, riesgo de seguridad o pérdida de datos, "
                "o si el usuario pidió expresamente que se le avise con alerta.",
            },
            "resumen_para_humano": {
                "type": "string",
                "description": "2-4 frases claras, sin markdown, pensadas para leerse en voz alta.",
            },
        },
        "required": ["es_critico", "resumen_para_humano"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _system_director(rama: str) -> str:
    return (
        f"Eres el Director de la rama '{rama}' de un asistente personal que trabaja en la máquina del usuario. "
        f"La carpeta de trabajo es {Config.WORKDIR}. Investiga con las herramientas disponibles "
        "(son de solo lectura) hasta tener datos reales; no inventes resultados. Si algo no se puede "
        "comprobar con ellas, dilo. Cuando termines, llama a emitir_resolucion con un resumen breve "
        "basado en lo que has visto."
    )


async def _llamar_director(rama: str, messages: list):
    kwargs = dict(
        model=Config.CLAUDE_DIRECTOR_MODEL,
        max_tokens=16000,
        system=_system_director(rama),
        tools=DEFINICIONES + [HERRAMIENTA_RESOLUCION],
        output_config={"effort": "medium"},
        messages=messages,
    )
    if Config.DIRECTOR_FALLBACKS:
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    return await claude.beta.messages.create(**kwargs)


async def _bucle_director(rama: str, instruccion: str) -> dict:
    messages = [{"role": "user", "content": instruccion}]
    for _ in range(MAX_ITERACIONES):
        resp = await _llamar_director(rama, messages)
        if resp.stop_reason == "refusal":
            raise RechazoModelo("El modelo rechazó la tarea")
        messages.append({"role": "assistant", "content": resp.content})

        llamadas = [b for b in resp.content if b.type == "tool_use"]
        for b in llamadas:
            if b.name == "emitir_resolucion":
                return b.input
        if not llamadas:
            messages.append({"role": "user", "content": "Termina llamando a emitir_resolucion."})
            continue

        async def _ejecutar(b):
            logger.info(f"  [{rama}] {b.name} {json.dumps(b.input, ensure_ascii=False)}")
            salida, es_error = await ejecutar_herramienta(b.name, b.input)
            return {"type": "tool_result", "tool_use_id": b.id, "content": salida, "is_error": es_error}

        resultados = await asyncio.gather(*(_ejecutar(b) for b in llamadas))
        messages.append({"role": "user", "content": list(resultados)})
    raise RuntimeError(f"El Director no terminó en {MAX_ITERACIONES} pasos")


async def ejecutar_rama(rama: str, instruccion: str, modo_voz: bool):
    async with _directores:
        logger.info(f"[*] Director {Config.CLAUDE_DIRECTOR_MODEL} en rama [{rama}]")
        try:
            resultado = await _bucle_director(rama, instruccion)
        except Exception as e:
            logger.exception(f"Error en la rama {rama}")
            await responder_por_telegram(f"Error en la rama {rama}: {e}", modo_voz=False)
            return

    resumen = resultado["resumen_para_humano"]
    _anotar("assistant", f"[Resultado de la rama {rama}] {resumen}")
    if resultado["es_critico"]:
        logger.warning(f"[ALERTA CRÍTICA en {rama}] {resumen}")
        enviada = await enviar_alerta_ntfy(resumen, titulo=f"Alerta crítica: {rama}")
        aviso = "Alerta enviada al móvil. " if enviada else "No se pudo enviar la alerta a ntfy. "
        await responder_por_telegram(f"{aviso}{resumen}", modo_voz=modo_voz)
    else:
        await responder_por_telegram(f"[{rama}] {resumen}", modo_voz=modo_voz)


# =====================================================================
# Recepcionista: respuesta inmediata + decisión de delegar
# =====================================================================
SYSTEM_FRONTAL = (
    "Eres el recepcionista de un asistente personal por Telegram. Respondes de forma natural, breve "
    "y sin markdown ni listas, porque tu respuesta puede leerse en voz alta. "
    "Si el usuario saluda o pregunta algo que puedes contestar tú, contéstale y pon delegar=false, "
    "rama='ninguna' e instruccion_sintetizada=''. "
    "Si pide un trabajo que requiere mirar su máquina (código, repositorios, estado del sistema, "
    "archivos...), confirma en una frase, pon delegar=true, elige la rama y escribe en "
    "instruccion_sintetizada una especificación completa y autocontenida para el Director, "
    "incluyendo el contexto de la conversación que necesite."
)

FORMATO_FRONTAL = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "respuesta_inmediata": {"type": "string"},
            "delegar": {"type": "boolean"},
            "rama": {"type": "string", "enum": RAMAS + ["ninguna"]},
            "instruccion_sintetizada": {"type": "string"},
        },
        "required": ["respuesta_inmediata", "delegar", "rama", "instruccion_sintetizada"],
        "additionalProperties": False,
    },
}


async def agente_frontal_recepcionista(texto_usuario: str, modo_voz: bool):
    _anotar("user", texto_usuario)
    try:
        resp = await claude.messages.create(
            model=Config.CLAUDE_FRONTAL_MODEL,
            max_tokens=4000,
            system=SYSTEM_FRONTAL,
            output_config={"effort": "low", "format": FORMATO_FRONTAL},
            messages=list(_historial),
        )
        if resp.stop_reason == "refusal":
            raise RechazoModelo("El modelo rechazó el mensaje")
        decision = json.loads(next(b.text for b in resp.content if b.type == "text"))
    except Exception as e:
        logger.exception("Error en el recepcionista")
        await responder_por_telegram(f"No he podido procesar el mensaje: {e}", modo_voz=False)
        return

    _anotar("assistant", decision["respuesta_inmediata"])
    await responder_por_telegram(decision["respuesta_inmediata"], modo_voz=modo_voz)

    if decision["delegar"] and decision["rama"] in RAMAS:
        instruccion = decision["instruccion_sintetizada"] or texto_usuario
        tarea = asyncio.create_task(ejecutar_rama(decision["rama"], instruccion, modo_voz))
        _tareas[tarea] = {"rama": decision["rama"], "instruccion": instruccion, "inicio": time.monotonic()}
        tarea.add_done_callback(lambda t: _tareas.pop(t, None))
