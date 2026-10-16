"""Lanza turnos de las sesiones de Claude Code (subjefe y empresas).

Cada participante tiene una sesión persistente de Claude Code: el primer turno
la crea con --session-id y los siguientes la reanudan con --resume, así que la
memoria la guarda el propio Claude Code.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

from buzon import Buzon
from config import BASE_DIR, Config
from empresas import Empresa

logger = logging.getLogger(__name__)

SILENCIO = "[SILENCIO]"


class ErrorSesion(Exception):
    pass


def prompt_subjefe(empresas: dict[str, Empresa]) -> str:
    lista = "\n".join(f"- {e.nombre}: {e.descripcion}" for e in empresas.values()) or "- (ninguna configurada)"
    return f"""Eres el SUBJEFE. Por encima de ti está el JEFE, la única persona con la que hablas. \
Por debajo tienes EMPRESAS: sesiones de Claude Code que hacen el trabajo y obedecen tus órdenes.

Tú no haces trabajo: no tienes herramientas para ello. Tu papel es:
- Entender lo que quiere el jefe y dar la orden a la empresa adecuada con la herramienta `encargar`. \
Las órdenes deben ser claras y autocontenidas: la empresa no ve tu conversación con el jefe. \
Cuando delegues algo que te pidió el jefe, confírmaselo en una frase.
- Recibir los informes de las empresas y decirle al jefe lo que necesita saber, resumido.
- Cuando una empresa pregunte algo que solo el jefe puede decidir, preguntárselo; cuando el jefe responda, \
pasarle la respuesta a esa empresa con `encargar`.
- Coordinar: si una tarea necesita varias empresas, repártela; si una empresa necesita algo de otra, encárgaselo.

Empresas disponibles:
{lista}

Los mensajes te llegan etiquetados: [JEFE] es el jefe; [EMPRESA x] es un informe de la empresa x; \
[URGENTE] marca algo que la empresa considera urgente.

Tu respuesta final de cada turno se le entrega al jefe tal cual, y puede leerse en voz alta: \
breve, natural, en español, sin markdown ni listas. Si en este turno no hay nada que merezca \
molestar al jefe, responde exactamente {SILENCIO}. Si algo es grave o no puede esperar, usa además \
`alerta_urgente`, que hace sonar su móvil."""


PROMPT_EMPRESA = """Eres la empresa "{nombre}". Trabajas a las órdenes del SUBJEFE, que es tu único \
interlocutor: el jefe nunca te lee directamente. Las órdenes te llegan etiquetadas [ORDEN DEL SUBJEFE].

Haz el trabajo con tus herramientas y organízate como quieras. Tu respuesta final de cada turno se \
le entrega al subjefe: resume qué has hecho y el resultado. Si necesitas una decisión del jefe, \
termina con la pregunta; la respuesta te llegará en una orden posterior.

Si en mitad de un trabajo largo hay algo que el subjefe deba saber ya, usa `informar_al_subjefe` \
(con urgente=true si es grave) y sigue trabajando."""


def _mcp_config(nombre: str, rol: str) -> Path:
    """Escribe el JSON de --mcp-config que conecta la sesión al buzón."""
    ruta = Config.DATA_DIR / f"mcp_{nombre}.json"
    conf = {
        "mcpServers": {
            "buzon": {
                "command": sys.executable,
                "args": [str(BASE_DIR / "mcp_server.py")],
                "env": {"ROL": rol, "EMPRESA": nombre, "DATA_DIR": str(Config.DATA_DIR)},
            }
        }
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(conf, indent=2), encoding="utf-8")
    return ruta


def args_subjefe(empresas: dict[str, Empresa]) -> list[str]:
    return [
        "--model", Config.SUBJEFE_MODEL,
        "--tools", "",                       # sin herramientas de trabajo: solo delega
        "--strict-mcp-config",
        "--mcp-config", str(_mcp_config("subjefe", "subjefe")),
        "--permission-mode", "dontAsk",
        "--append-system-prompt", prompt_subjefe(empresas),
        "--allowedTools", "mcp__buzon",
    ]


def args_empresa(e: Empresa) -> list[str]:
    args = [
        "--model", e.modelo,
        "--permission-mode", e.permission_mode,
        "--mcp-config", str(_mcp_config(e.nombre, "empresa")),
        "--append-system-prompt", PROMPT_EMPRESA.format(nombre=e.nombre),
    ]
    if e.max_budget_usd:
        args += ["--max-budget-usd", str(e.max_budget_usd)]
    args += ["--allowedTools", "mcp__buzon", *e.allowed_tools]
    return args


async def ejecutar_turno(buzon: Buzon, nombre: str, prompt: str, cwd: Path, args: list[str], timeout: int) -> str:
    """Ejecuta un turno `claude -p` en la sesión persistente de `nombre` y devuelve su respuesta."""
    sid, iniciada = buzon.sesion(nombre)
    cmd = [Config.CLAUDE_BIN, "-p", "--output-format", "json",
           *(["--resume", sid] if iniciada else ["--session-id", sid]), *args]
    cwd.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ErrorSesion(f"{nombre}: el turno superó {timeout}s y se canceló")

    try:
        datos = json.loads(out.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        detalle = err.decode("utf-8", errors="replace").strip()[-500:]
        raise ErrorSesion(f"{nombre}: Claude Code terminó con código {proc.returncode}. {detalle}")

    buzon.marcar_sesion_iniciada(nombre)  # si hay JSON, la sesión existe
    if datos.get("is_error"):
        raise ErrorSesion(f"{nombre}: {datos.get('result') or datos.get('subtype')}")
    return (datos.get("result") or "").strip()
