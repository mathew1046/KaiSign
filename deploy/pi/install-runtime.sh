#!/usr/bin/env sh
set -eu

PROJECT_ROOT="${PROJECT_ROOT:-/home/kaizen/kaizen/custom_dataset_camera}"
BACKEND_DIR="$PROJECT_ROOT/backend"
TMP_DIR="$PROJECT_ROOT/.pip-tmp"
UV="${UV_BIN:-$HOME/.local/bin/uv}"
VENV_DIR="$BACKEND_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$TMP_DIR"
cd "$BACKEND_DIR"

if [ ! -x "$UV" ]; then
  printf 'uv executable not found: %s\nSet UV_BIN to the uv path or install uv for the kaizen user.\n' "$UV" >&2
  exit 1
fi

rm -rf "$VENV_DIR"
"$UV" python install 3.12
"$UV" venv --python 3.12 "$VENV_DIR"

TMPDIR="$TMP_DIR" "$UV" pip install --python "$VENV_PYTHON" --only-binary=:all: --no-cache -r requirements-pi-base.txt
TMPDIR="$TMP_DIR" "$UV" pip install --python "$VENV_PYTHON" --only-binary=:all: --no-cache --no-deps -r requirements-pi-mediapipe.txt

"$VENV_PYTHON" - <<'PY'
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

print("mediapipe", mp.__version__)
print("tasks", BaseOptions.__name__, vision.__name__)
PY
