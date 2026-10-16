#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d venv ] || ./install.sh
exec ./venv/bin/python main.py
