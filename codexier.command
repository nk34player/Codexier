#!/bin/zsh

# Launch Codexier from this file's directory.
set -u
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  exec "$SCRIPT_DIR/.venv/bin/python" codexier/main.py "$@"
fi

exec python3 codexier/main.py "$@"
