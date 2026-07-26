#!/bin/zsh

# Launch Codexier from this file's directory.
set -u
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" || "${TERM_PROGRAM:-}" == "iTerm.app" ]]; then
  # Request a readable terminal size for the full-screen TUI.
  printf '\033[8;50;160t'
fi

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  "$SCRIPT_DIR/.venv/bin/python" codexier/main.py "$@"
  STATUS=$?
else
  python3 codexier/main.py "$@"
  STATUS=$?
fi

printf '\nCodexier finished. Press Enter to close this window.'
read -r
exit "$STATUS"
