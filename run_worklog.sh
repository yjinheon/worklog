#!/usr/bin/env bash

# run-worklog.sh
set -euo pipefail

DATE="${1:-$(date +%F)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RAW="/tmp/worklog-${DATE}.raw.md"

echo "==> [1/2] extracting git log for ${DATE}"
OUTPUT="$RAW" "$SCRIPT_DIR/daily_worklog.py" "$DATE"

if [[ ! -s "$RAW" ]]; then
  echo "no commits found for ${DATE}, exiting" >&2
  exit 0
fi

echo "==> [2/2] refining with LLM"
"$SCRIPT_DIR/refine_worklog.sh" "$RAW"

echo "==> done"
