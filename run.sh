#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d venv ] || ./install.sh
export PYTHONUTF8=1
exec ./venv/bin/python main.py
