#!/usr/bin/env bash
# Instalador para Linux, WSL2 y macOS (opción secundaria; el camino principal es install.ps1 en Windows).
# Opciones por variable de entorno:
#   PYTHON=python3.12   fuerza el intérprete
#   TORCH_CPU=1         instala torch solo CPU (evita las ruedas CUDA, mucho más pesadas)
set -euo pipefail
cd "$(dirname "$0")"

echo "== Instalador: Claude Voice Swarm =="

if grep -qi microsoft /proc/version 2>/dev/null; then
    echo "Detectado WSL."
    case "$(pwd)" in
        /mnt/*) echo "AVISO: el repositorio está en el disco de Windows ($(pwd)). Clónalo dentro de WSL (p. ej. ~/claude-voice-swarm)." ;;
    esac
    [ -d /run/systemd/system ] || echo "AVISO: systemd no está activo. Ver README, 'Despliegue en WSL2'."
fi

# kokoro exige Python >=3.10,<3.13.
version_ok() { "$1" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for c in python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null && version_ok "$c"; then PY="$c"; break; fi
    done
fi
if [ -z "$PY" ] || ! version_ok "$PY"; then
    echo "ERROR: hace falta Python 3.10, 3.11 o 3.12 (kokoro no funciona en 3.13+)."
    echo "Ubuntu 24.04 trae 3.12 y Ubuntu 22.04 trae 3.10. En otras versiones, instala uno de ellos y usa PYTHON=..."
    exit 1
fi
PYV="$("$PY" -c 'import sys; print("python%d.%d" % sys.version_info[:2])')"
echo "Usando $PY ($PYV)"

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Instalando dependencias del sistema (ffmpeg, espeak-ng, venv)..."
    sudo apt-get update -y
    sudo apt-get install -y ffmpeg espeak-ng "${PYV}-venv" python3-pip
elif [[ "$OSTYPE" == "darwin"* ]]; then
    command -v brew >/dev/null || { echo "Instala Homebrew desde https://brew.sh/"; exit 1; }
    brew install ffmpeg espeak-ng
else
    echo "Sistema no reconocido: instala ffmpeg y espeak-ng a mano."
fi

[ -d venv ] || "$PY" -m venv venv
if ! version_ok ./venv/bin/python; then
    echo "ERROR: el venv existente no usa Python 3.10-3.12. Bórralo (rm -rf venv) y repite."
    exit 1
fi
./venv/bin/pip install --upgrade pip
if [ "${TORCH_CPU:-0}" = "1" ]; then
    ./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
fi
./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    echo
    echo "== Configuración (.env) =="
    read -rsp "TELEGRAM_TOKEN (de @BotFather, no se muestra): " tg_token; echo
    read -rp "AUTHORIZED_CHAT_ID (déjalo vacío y el bot te lo dirá al escribirle): " tg_chat
    topic="cvs-$("$PY" -c 'import secrets; print(secrets.token_hex(12))')"

    umask 077
    sed -e "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=${tg_token}|" \
        -e "s|^AUTHORIZED_CHAT_ID=.*|AUTHORIZED_CHAT_ID=${tg_chat:-0}|" \
        -e "s|^NTFY_TOPIC=.*|NTFY_TOPIC=${topic}|" \
        .env.example > .env
    chmod 600 .env
    echo ".env creado. Tu tema de ntfy es: ${topic}"
    echo "Suscríbete a ese tema en la app ntfy del móvil (servidor ntfy.sh)."
else
    echo ".env ya existe, se conserva."
fi

if [ ! -f empresas.json ]; then
    cp empresas.json.example empresas.json
    echo "empresas.json creado a partir del ejemplo: edítalo con tus empresas y sus carpetas."
fi

CLAUDE_PATH="$(command -v claude || true)"
if [ -z "$CLAUDE_PATH" ]; then
    echo "AVISO: no encuentro 'claude'. Instala Claude Code dentro de este sistema y ejecuta 'claude' una vez para iniciar sesión."
elif [[ "$CLAUDE_PATH" == /mnt/* || "$CLAUDE_PATH" == *.exe ]]; then
    echo "AVISO: 'claude' es el de Windows ($CLAUDE_PATH). Instala Claude Code dentro de WSL."
fi

echo
echo "== Listo =="
echo "1. Revisa empresas.json (opcional) y los topes de gasto de .env"
echo "2. Prueba con ./run.sh; como servicio, ver deploy/claude-voice-swarm.service y el README"
