import os
import shlex
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_FRONTAL_MODEL = os.getenv("CLAUDE_FRONTAL_MODEL", "claude-haiku-5-5")
    CLAUDE_DIRECTOR_MODEL = os.getenv("CLAUDE_DIRECTOR_MODEL", "claude-sonnet-5-5")
    DIRECTOR_FALLBACKS = os.getenv("DIRECTOR_FALLBACKS", "1") == "1"

    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    AUTHORIZED_CHAT_ID = int(os.getenv("AUTHORIZED_CHAT_ID", "0") or "0")

    NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
    NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

    WORKDIR = Path(os.getenv("WORKDIR", ".")).expanduser().resolve()
    ALLOWED_COMMANDS = [
        shlex.split(c) for c in os.getenv("ALLOWED_COMMANDS", "").split(";") if c.strip()
    ]

    KOKORO_VOICE = os.getenv("KOKORO_VOICE", "ef_dora")
    KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.05"))
    WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
