#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== Instalador: Claude Voice Swarm =="

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Instalando dependencias del sistema (ffmpeg, espeak-ng)..."
    sudo apt-get update -y
    sudo apt-get install -y ffmpeg espeak-ng python3-venv python3-pip
elif [[ "$OSTYPE" == "darwin"* ]]; then
    command -v brew >/dev/null || { echo "Instala Homebrew desde https://brew.sh/"; exit 1; }
    brew install ffmpeg espeak-ng
else
    echo "Sistema no reconocido: instala ffmpeg y espeak-ng a mano."
fi

PY="${PYTHON:-python3}"
"$PY" -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    echo
    echo "== Configuración (.env) =="
    read -rp "ANTHROPIC_API_KEY: " ant_key
    read -rp "TELEGRAM_TOKEN (de @BotFather): " tg_token
    read -rp "AUTHORIZED_CHAT_ID (déjalo vacío y el bot te lo dirá al escribirle): " tg_chat
    read -rp "Carpeta de trabajo del Director [$HOME]: " workdir
    topic="cvs-$("$PY" -c 'import secrets; print(secrets.token_hex(12))')"

    sed -e "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ant_key}|" \
        -e "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=${tg_token}|" \
        -e "s|^AUTHORIZED_CHAT_ID=.*|AUTHORIZED_CHAT_ID=${tg_chat:-0}|" \
        -e "s|^NTFY_TOPIC=.*|NTFY_TOPIC=${topic}|" \
        -e "s|^WORKDIR=.*|WORKDIR=${workdir:-$HOME}|" \
        .env.example > .env
    chmod 600 .env
    echo ".env creado. Tu tema de ntfy es: ${topic}"
    echo "Suscríbete a ese tema en la app ntfy del móvil (servidor ntfy.sh)."
else
    echo ".env ya existe, se conserva."
fi

echo
echo "== Listo =="
echo "Arrancar:            ./run.sh"
echo "Conectar Claude Code: claude mcp add voice-comms -- \"$(pwd)/venv/bin/python\" \"$(pwd)/mcp_server.py\""
