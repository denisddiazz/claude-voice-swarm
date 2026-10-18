import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# El servidor MCP de una empresa o de una sesión externa y el hook del subjefe en el PC no
# necesitan ningún secreto: no cargan .env (el puente o el registro les pasan las rutas resueltas).
if os.getenv("ROL") not in ("empresa", "externa", "hook_pc"):
    load_dotenv(ENV_FILE)

_SUBJEFE_DIR_DEFECTO = r"C:\Cositas\Subjefe" if os.name == "nt" else "~/Subjefe"


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


class Config:
    # --- Claude Code ---
    CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
    SUBJEFE_MODEL = os.getenv("SUBJEFE_MODEL", "haiku")
    # --effort de claude -p (low, medium, high, xhigh, max); vacío = el de Claude Code.
    SUBJEFE_EFFORT = os.getenv("SUBJEFE_EFFORT", "medium").strip().lower()
    SUBJEFE_TIMEOUT = int(os.getenv("SUBJEFE_TIMEOUT", "300"))
    EMPRESA_TIMEOUT = int(os.getenv("EMPRESA_TIMEOUT", "3600"))
    EMPRESAS_FILE = ruta_proyecto(os.getenv("EMPRESAS_FILE"), BASE_DIR / "empresas.json")
    DATA_DIR = ruta_proyecto(os.getenv("DATA_DIR"), BASE_DIR / "datos")
    # Carpeta de trabajo del subjefe: su CLAUDE.md fija su papel y .claude/skills sus skills.
    SUBJEFE_DIR = ruta_proyecto(os.getenv("SUBJEFE_DIR"), Path(_SUBJEFE_DIR_DEFECTO))
    # Transcripciones de Claude Code (~/.claude/projects o CLAUDE_CONFIG_DIR/projects).
    CLAUDE_PROJECTS_DIR = Path(os.getenv("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"
    # Sesiones externas: no se reanudan si su conversación tuvo actividad fuera del puente hace
    # menos de estos minutos (probablemente está abierta en la app). 0 = sin comprobación.
    EXTERNA_MARGEN_MIN = _float("EXTERNA_MARGEN_MIN", "10")
    # Temas del subjefe: cada tema es una conversación; al cerrarse, la siguiente empieza de cero
    # con un resumen. Se cierra tras estos minutos sin mensajes del jefe (0 = nunca por inactividad),
    # si su transcripción supera estos KB (0 = sin límite) o a petición del subjefe o del jefe.
    TEMA_INACTIVIDAD_MIN = _float("TEMA_INACTIVIDAD_MIN", "120")
    TEMA_MAX_KB = _float("TEMA_MAX_KB", "600")
    TEMA_ESTADO_MAX_CHARS = int(_float("TEMA_ESTADO_MAX_CHARS", "1500"))  # estado inyectado al empezar

    # --- Canal PC (ver README, "Canal PC") ---
    # Sesión de la app de escritorio abierta en SUBJEFE_DIR: es el subjefe, con el que el jefe
    # habla directamente en el PC. SUBJEFE_DIR lleva un .mcp.json (ROL=subjefe_pc) y un hook
    # UserPromptSubmit que escribe el puente. El título es el nombre con que la sesión aparece en
    # ListAgents. Vacío = canal PC desactivado (todo por Telegram).
    SUBJEFE_PC_TITULO = os.getenv("SUBJEFE_PC_TITULO", "Subjefe").strip()
    # Minutos sin actividad en el PC (teclado o ratón; si no se puede medir, sin mensajes del jefe
    # por el PC) tras los que el canal pasa solo al móvil. 0 = nunca por inactividad.
    PC_INACTIVIDAD_MIN = _float("PC_INACTIVIDAD_MIN", "15")
    # "entrada": teclado y ratón de Windows (GetLastInputInfo); "mensajes": solo los mensajes del
    # jefe por el PC (por si algún programa simula entrada y el PC nunca parece inactivo).
    PC_INACTIVIDAD_FUENTE = os.getenv("PC_INACTIVIDAD_FUENTE", "entrada").strip().lower()
    RELE_PC_MODELO = os.getenv("RELE_PC_MODELO", "haiku")
    RELE_PC_TIMEOUT = int(_float("RELE_PC_TIMEOUT", "120"))
    RELE_PC_MAX_BUDGET_USD = _float("RELE_PC_MAX_BUDGET_USD", "0.05")  # por entrega; 0 = sin tope

    # --- Gasto (opcional; 0 = sin tope) ---
    SUBJEFE_MAX_BUDGET_USD = _float("SUBJEFE_MAX_BUDGET_USD", "0")  # por turno del subjefe
    EMPRESA_MAX_BUDGET_USD = _float("EMPRESA_MAX_BUDGET_USD", "0")  # por turno de cada sesión, si no fija el suyo
    TOPE_DIARIO_USD = _float("TOPE_DIARIO_USD", "0")  # suma de todas las sesiones en el día local
    MAX_REBOTES = int(_float("MAX_REBOTES", "3"))  # encargos encadenados sin un mensaje del jefe; 0 = sin límite
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


# --- Entorno de las sesiones de Claude Code ---
# Las sesiones de abajo son sesiones normales: heredan el entorno del usuario, salvo los
# secretos del puente. No pasa ninguna variable definida en .env ni con estos prefijos.
_PREFIJOS_PROHIBIDOS = ("TELEGRAM_", "NTFY_")


def entorno_sesion(origen: dict | None = None, env_file: Path | None = None) -> dict[str, str]:
    """Entorno para los procesos `claude` (y, por herencia, sus comandos y servidores MCP)."""
    origen = os.environ if origen is None else origen
    env_file = ENV_FILE if env_file is None else env_file
    del_env = {k.upper() for k in (dotenv_values(env_file) if Path(env_file).exists() else {})}
    return {k: v for k, v in origen.items()
            if k.upper() not in del_env and not k.upper().startswith(_PREFIJOS_PROHIBIDOS)}
