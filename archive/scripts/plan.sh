#!/usr/bin/env bash
set -euo pipefail

TOP="${1:-3}"
SCRIPT_DIR=".claude/skills/next-task-suggester/scripts"
DB="${HOME}/.local/state/next-task-suggester/cache.db"

JSON=$(uv run "$SCRIPT_DIR/suggest.py" --json --top "$TOP" | grep -v '^#')
REPOS=$(echo "$JSON" | python3 -c "import json,sys;[print(r['repo']) for r in json.load(sys.stdin)]")

{
  echo "# 추천 후보 (JSON)"
  echo "$JSON"
  echo
  echo "# 후보별 최근 커밋 (최신 5개)"
  for r in $REPOS; do
    echo
    echo "[$r]"
    sqlite3 "$DB" "SELECT '  - '||subject FROM commit_log WHERE repo='$r' ORDER BY ts DESC LIMIT 5;"
  done
} | claude -p --model claude-sonnet-4-6 \
    "각 후보별로 (1) 왜 오늘 이 프로젝트를 진행하면 좋은지, (2) 오늘 할 작업 1-2개를 commit message와 project 정보 근거로 한국어 2-3줄씩만 짧게 요약해."
