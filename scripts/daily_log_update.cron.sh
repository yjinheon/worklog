#!/usr/bin/env bash
# Thin wrapper for the daily-log auto-update cron job (no_agent mode).
# Lives in the project; ~/.hermes/scripts/daily_log_update.sh symlinks here
# (the cron scheduler only accepts filenames under ~/.hermes/scripts/).
set -euo pipefail
# Resolve the real script location (this file may be reached via a symlink
# in ~/.hermes/scripts/), so daily_log_update.py is found in the project.
SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do
  D="$(cd "$(dirname "$SRC")" && pwd)"
  T="$(readlink "$SRC")"
  case "$T" in /*) SRC="$T" ;; *) SRC="$D/$T" ;; esac
done
DIR="$(cd "$(dirname "$SRC")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
exec "$PY" "$DIR/daily_log_update.py" --quiet
