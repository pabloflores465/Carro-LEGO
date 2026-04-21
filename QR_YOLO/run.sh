#!/usr/bin/env bash
# run.sh — Lanza el sistema QR Robot con el venv correcto.
#
# Uso:
#   ./run.sh              # modo real (NXT + cámara)
#   ./run.sh --simulate   # modo simulación (sin hardware)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# libzbar en macOS Apple Silicon via Homebrew
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:${DYLD_LIBRARY_PATH:-}"

# Activa el venv
source "$SCRIPT_DIR/venv/bin/activate"

exec python main.py "$@"
