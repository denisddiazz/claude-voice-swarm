"""Herramientas reales que el Director puede usar.

Todo es de solo lectura y está acotado a WORKDIR. Los comandos se ejecutan sin
shell y solo si empiezan por uno de los prefijos de ALLOWED_COMMANDS.
"""
import asyncio
from pathlib import Path

from config import Config

MAX_SALIDA = 8000
TIMEOUT_COMANDO = 60
ARCHIVOS_PROHIBIDOS = {".env", "id_rsa", "id_ed25519"}

DEFINICIONES = [
    {
        "name": "ejecutar_comando",
        "description": (
            "Ejecuta un comando de diagnóstico en la máquina del usuario y devuelve su salida. "
            "Solo se aceptan comandos de la lista permitida: "
            + "; ".join(" ".join(c) for c in Config.ALLOWED_COMMANDS)
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Comando y argumentos por separado, p. ej. ["git", "log", "-5"]',
                }
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "leer_archivo",
        "description": "Lee un archivo de texto dentro de la carpeta de trabajo (ruta relativa).",
        "input_schema": {
            "type": "object",
            "properties": {"ruta": {"type": "string"}},
            "required": ["ruta"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "listar_directorio",
        "description": "Lista el contenido de un directorio dentro de la carpeta de trabajo (ruta relativa, '.' para la raíz).",
        "input_schema": {
            "type": "object",
            "properties": {"ruta": {"type": "string"}},
            "required": ["ruta"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def _recortar(texto: str) -> str:
    return texto if len(texto) <= MAX_SALIDA else texto[:MAX_SALIDA] + "\n[...salida recortada...]"


def _ruta_segura(ruta: str) -> Path:
    destino = (Config.WORKDIR / ruta).resolve()
    if not destino.is_relative_to(Config.WORKDIR):
        raise PermissionError(f"Ruta fuera de la carpeta de trabajo: {ruta}")
    if destino.name in ARCHIVOS_PROHIBIDOS:
        raise PermissionError(f"Archivo protegido: {destino.name}")
    return destino


def _comando_permitido(argv: list[str]) -> bool:
    return any(argv[: len(p)] == p for p in Config.ALLOWED_COMMANDS)


async def ejecutar_comando(argv: list[str]) -> str:
    if not argv or not _comando_permitido(argv):
        raise PermissionError(f"Comando no permitido: {' '.join(argv)}")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=Config.WORKDIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        salida, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_COMANDO)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"El comando tardó más de {TIMEOUT_COMANDO}s")
    texto = salida.decode("utf-8", errors="replace")
    return _recortar(f"[código de salida {proc.returncode}]\n{texto}")


async def leer_archivo(ruta: str) -> str:
    destino = _ruta_segura(ruta)
    return _recortar(destino.read_text(encoding="utf-8", errors="replace"))


async def listar_directorio(ruta: str) -> str:
    destino = _ruta_segura(ruta)
    entradas = sorted(destino.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    return _recortar("\n".join(f"{p.name}/" if p.is_dir() else p.name for p in entradas) or "(vacío)")


_HANDLERS = {
    "ejecutar_comando": ejecutar_comando,
    "leer_archivo": leer_archivo,
    "listar_directorio": listar_directorio,
}


async def ejecutar_herramienta(nombre: str, entrada: dict) -> tuple[str, bool]:
    """Devuelve (resultado, es_error)."""
    handler = _HANDLERS.get(nombre)
    if handler is None:
        return f"Herramienta desconocida: {nombre}", True
    try:
        return await handler(**entrada), False
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
