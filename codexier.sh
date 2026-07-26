#!/usr/bin/env sh

# Launch Codexier from this file's directory on Linux.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  exec "$SCRIPT_DIR/.venv/bin/python" codexier/main.py "$@"
fi

exec python3 codexier/main.py "$@"
