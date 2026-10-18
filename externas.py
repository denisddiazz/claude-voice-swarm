"""Sesiones externas: sesiones normales de Claude Code (por ejemplo, de la app de escritorio)
que se dan de alta en el buzón la primera vez que llaman a `informar_al_subjefe`.

Sin dependencia del paquete mcp (se prueba aparte). Las usan el servidor MCP en modo
ROL=externa (identificar la sesión que llama) y el puente (no reanudar una conversación
que está en uso fuera de él).
"""
import json
import re
import time
from pathlib import Path

from config import Config
from empresas import NOMBRE_VALIDO, RESERVADOS, SESSION_ID_VALIDO

VAR_SESSION_ID = "CLAUDE_CODE_SESSION_ID"  # la fija Claude Code en sus procesos hijos (no documentada)
_VENTANA_RECIENTES_S = 30 * 60  # transcripciones candidatas: modificadas en la última media hora
_COLA_BYTES = 256 * 1024        # cuánto del final de cada transcripción se mira


def codificar_carpeta(directorio) -> str:
    """Nombre de la carpeta de ~/.claude/projects para un cwd: todo lo que no es letra o cifra, '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(directorio))


def carpeta_transcripciones(directorio) -> Path:
    return Config.CLAUDE_PROJECTS_DIR / codificar_carpeta(directorio)


def transcripcion(directorio, session_id: str) -> Path:
    return carpeta_transcripciones(directorio) / f"{session_id}.jsonl"


def _cola(ruta: Path) -> str:
    try:
        with open(ruta, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - _COLA_BYTES))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def identificar_sesion(directorio, env: dict, mensaje: str = "", ahora: float | None = None
                       ) -> tuple[str | None, str]:
    """Devuelve (session_id, método) de la sesión que llama. Métodos, de más a menos fiable:

    - "entorno": variable CLAUDE_CODE_SESSION_ID que hereda el servidor MCP de Claude Code, si
      su transcripción está en esa carpeta (una variable heredada de otra sesión no lo estaría).
    - "transcripcion": la única transcripción reciente de esa carpeta que contiene el mensaje.
    - "reciente": la transcripción modificada más recientemente en esa carpeta. Si hay dos
      sesiones abiertas en la misma carpeta, puede no ser la que llama.
    - "entorno" también si la variable existe pero la carpeta no tiene transcripciones.
    - "desconocido": ninguna de las anteriores (session_id None).
    """
    sid_env = (env.get(VAR_SESSION_ID) or "").strip().lower()
    if not SESSION_ID_VALIDO.match(sid_env):
        sid_env = ""
    if sid_env and transcripcion(directorio, sid_env).exists():
        return sid_env, "entorno"

    ahora = time.time() if ahora is None else ahora
    candidatas = []
    for f in carpeta_transcripciones(directorio).glob("*.jsonl"):
        if not SESSION_ID_VALIDO.match(f.stem):
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        candidatas.append((mtime, f))
    if not candidatas:
        return (sid_env, "entorno") if sid_env else (None, "desconocido")
    candidatas.sort(reverse=True)

    trozo = json.dumps((mensaje or "")[:200], ensure_ascii=False)[1:-1]
    if trozo:
        con_mensaje = [f for m, f in candidatas if ahora - m < _VENTANA_RECIENTES_S and trozo in _cola(f)]
        if len(con_mensaje) == 1:
            return con_mensaje[0].stem.lower(), "transcripcion"
    return candidatas[0][1].stem.lower(), "reciente"


def nombre_por_defecto(directorio, ocupados) -> str:
    """Nombre válido a partir del de la carpeta; si ya existe, se añade -2, -3..."""
    base = re.sub(r"[^a-z0-9_-]+", "-", Path(str(directorio)).name.lower()).strip("-_")[:36] or "sesion"
    if base in RESERVADOS:
        base = f"{base}-sesion"
    nombre, n = base, 2
    while nombre in ocupados or not NOMBRE_VALIDO.match(nombre):
        nombre, n = f"{base}-{n}", n + 1
    return nombre


def actividad_ajena(buzon, nombre: str, directorio, session_id: str, margen_s: float,
                    ahora: float | None = None) -> float | None:
    """Segundos desde que la conversación se modificó fuera del puente, si fue hace menos de
    `margen_s`; None si no hay actividad reciente ajena (o no se puede saber)."""
    if margen_s <= 0:
        return None
    try:
        mtime = transcripcion(directorio, session_id).stat().st_mtime
    except OSError:
        return None
    fila = buzon.info_sesion(nombre)
    propio = fila["mtime_puente"] if fila is not None and fila["session_id"] == session_id else None
    if propio is not None and mtime <= propio + 1:
        return None  # la última escritura fue del propio puente
    hace = (time.time() if ahora is None else ahora) - mtime
    return hace if hace < margen_s else None


def anotar_mtime_puente(buzon, nombre: str, directorio, session_id: str):
    """Tras un turno del puente: su escritura en la transcripción no cuenta como actividad ajena."""
    try:
        buzon.registrar_mtime_puente(nombre, transcripcion(directorio, session_id).stat().st_mtime)
    except OSError:
        pass
