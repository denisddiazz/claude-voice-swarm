import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # --- Claude Code ---
    CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
    SUBJEFE_MODEL = os.getenv("SUBJEFE_MODEL", "haiku")
    SUBJEFE_TIMEOUT = int(os.getenv("SUBJEFE_TIMEOUT", "300"))
    EMPRESA_TIMEOUT = int(os.getenv("EMPRESA_TIMEOUT", "3600"))
    EMPRESAS_FILE = Path(os.getenv("EMPRESAS_FILE", BASE_DIR / "empresas.json")).expanduser()
    DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "datos")).expanduser().resolve()

    # --- Telegram ---
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    AUTHORIZED_CHAT_ID = int(os.getenv("AUTHORIZED_CHAT_ID", "0") or "0")

    # --- ntfy ---
    NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
    NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

    # --- Voz local ---
    KOKORO_VOICE = os.getenv("KOKORO_VOICE", "ef_dora")
    KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.05"))
    WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")

    BUZON_DB = DATA_DIR / "buzon.sqlite3"
