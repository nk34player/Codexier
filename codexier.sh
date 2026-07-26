#!/usr/bin/env sh

# Launch Codexier from this file's directory on Linux.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if [ -t 1 ]; then
  printf '\033[8;50;160t'
fi

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  "$SCRIPT_DIR/.venv/bin/python" codexier/main.py "$@"
  STATUS=$?
else
  python3 codexier/main.py "$@"
  STATUS=$?
fi

printf '\nCodexier finished. Press Enter to close this window.'
read -r
exit "$STATUS"
