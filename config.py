import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# El servidor MCP de una empresa no necesita ningún secreto: no carga .env.
if os.getenv("ROL") != "empresa":
    load_dotenv(ENV_FILE)


def ruta_proyecto(valor, por_defecto: Path) -> Path:
    """Resuelve una ruta de configuración. Las relativas se toman respecto a la raíz
    del proyecto, nunca respecto al directorio de trabajo del proceso (el servidor MCP
    lo lanza Claude Code desde la carpeta de la sesión)."""
    ruta = Path(valor).expanduser() if valor else Path(por_defecto)
    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta
    return ruta.resolve()


def _float(nombre: str, por_defecto: str) -> float:
    return float(os.getenv(nombre, por_defecto) or por_defecto)


def _lista(nombre: str) -> list[str]:
    return [x.strip() for x in os.getenv(nombre, "").split(",") if x.strip()]


class Config:
    # --- Claude Code ---
    CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
    SUBJEFE_MODEL = os.getenv("SUBJEFE_MODEL", "haiku")
    SUBJEFE_TIMEOUT = int(os.getenv("SUBJEFE_TIMEOUT", "300"))
    EMPRESA_TIMEOUT = int(os.getenv("EMPRESA_TIMEOUT", "3600"))
    EMPRESAS_FILE = ruta_proyecto(os.getenv("EMPRESAS_FILE"), BASE_DIR / "empresas.json")
    DATA_DIR = ruta_proyecto(os.getenv("DATA_DIR"), BASE_DIR / "datos")
    # Variables de entorno adicionales que pasan a las sesiones (lista blanca, separada por comas).
    # Los nombres que estén en .env, TELEGRAM_* y NTFY_* no pasan nunca.
    ENTORNO_EXTRA = _lista("ENTORNO_EXTRA")

    # --- Gasto ---
    SUBJEFE_MAX_BUDGET_USD = _float("SUBJEFE_MAX_BUDGET_USD", "0.5")  # por turno del subjefe
    TOPE_DIARIO_USD = _float("TOPE_DIARIO_USD", "10")  # suma de todas las sesiones en el día local; 0 = sin tope
    MAX_REBOTES = int(os.getenv("MAX_REBOTES", "3"))  # encargos encadenados sin un mensaje del jefe
    SESION_MAX_FALLOS = int(os.getenv("SESION_MAX_FALLOS", "3"))  # fallos seguidos antes de recrear la sesión

    # --- Telegram ---
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    AUTHORIZED_CHAT_ID = int(os.getenv("AUTHORIZED_CHAT_ID", "0") or "0")

    # --- ntfy ---
    NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
    NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")
    ALERTA_INTERVALO_MIN = int(os.getenv("ALERTA_INTERVALO_MIN", "10"))

    # --- Voz local ---
    KOKORO_VOICE = os.getenv("KOKORO_VOICE", "ef_dora")
    KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.05"))
    WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")

    BUZON_DB = DATA_DIR / "buzon.sqlite3"


# --- Entorno de las sesiones de Claude Code (lista blanca) ---
_ENTORNO_BASE = {
    # POSIX
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "TEMP", "TMP",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "COMPUTERNAME",
    # Claude Code
    "CLAUDE_CONFIG_DIR",
}
_PREFIJOS_PROHIBIDOS = ("TELEGRAM_", "NTFY_")


def entorno_sesion(origen: dict | None = None, extra: list[str] | None = None,
                   env_file: Path | None = None) -> dict[str, str]:
    """Entorno para los procesos `claude` (y, por herencia, sus comandos y servidores MCP).

    Solo pasan las variables de la lista blanca (más LC_* y ENTORNO_EXTRA). Ninguna variable
    definida en .env ni con prefijo TELEGRAM_/NTFY_ pasa, aunque se pida en ENTORNO_EXTRA."""
    origen = os.environ if origen is None else origen
    extra = Config.ENTORNO_EXTRA if extra is None else extra
    env_file = ENV_FILE if env_file is None else env_file
    del_env = {k.upper() for k in (dotenv_values(env_file) if Path(env_file).exists() else {})}
    permitidas = _ENTORNO_BASE | {x.upper() for x in extra}
    res = {}
    for k, v in origen.items():
        ku = k.upper()
        if ku in del_env or ku.startswith(_PREFIJOS_PROHIBIDOS):
            continue
        if ku in permitidas or ku.startswith("LC_"):
            res[k] = v
    return res
