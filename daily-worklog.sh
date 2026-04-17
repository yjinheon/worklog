#!/usr/bin/env bash

# daily-worklog.sh — 여러 repo의 일별 commit 로그를 raw 마크다운으로 추출
set -euo pipefail

# ===== 인자/설정 =====
DATE="${1:-$(date +%F)}"
SINCE="${DATE} 00:00:00"
UNTIL="${DATE} 23:59:59"

AUTHOR="${WORKLOG_AUTHOR:-$(git config --global user.name 2>/dev/null || echo "$USER")}"
REPOS_FILE="${REPOS_FILE:-$HOME/workspace/worklog/repos.conf}"
OUTPUT="${OUTPUT:-/tmp/worklog-${DATE}.raw.md}"
MAX_COMMITS_FOR_STAT="${MAX_COMMITS_FOR_STAT:-8}"

# ===== 사전 점검 =====
[[ -f "$REPOS_FILE" ]] || { echo "ERROR: repos file not found: $REPOS_FILE" >&2; exit 1; }

# 주석/빈 줄 제외하고 repo 목록 로드
mapfile -t REPOS < <(grep -vE '^\s*(#|$)' "$REPOS_FILE")
[[ "${#REPOS[@]}" -gt 0 ]] || { echo "ERROR: no repos in $REPOS_FILE" >&2; exit 1; }

# ===== 본문 =====
{
  echo "# Worklog raw ${DATE}"
  echo ""
  #echo "_author: ${AUTHOR}_"
  echo ""

  for repo in "${REPOS[@]}"; do
    if [[ ! -d "$repo/.git" ]]; then
      echo "<!-- skip: $repo (not a git repo) -->"
      continue
    fi
    repo_name=$(basename "$repo")

    # 커밋 수 먼저 확인
    count=$(git -C "$repo" log \
      --since="$SINCE" --until="$UNTIL" \
  #    --author="$AUTHOR"  \
      --no-merges \
      --pretty=format:"%h" 2>/dev/null | grep -c . || true)

    [[ "$count" -eq 0 ]] && continue

    echo "## ${repo_name} (${count} commits)"
    echo ""
    echo '```'

    if [[ "$count" -le "$MAX_COMMITS_FOR_STAT" ]]; then
      git -C "$repo" log \
        --since="$SINCE" --until="$UNTIL" \
#        --author="$AUTHOR" --no-merges \ 
        --pretty=format:"%h %s" --shortstat
    else
      git -C "$repo" log \
        --since="$SINCE" --until="$UNTIL" \
#        --author="$AUTHOR" --no-merges \
        --pretty=format:"%h %s"
      echo ""
      echo "--- summary ---"
      git -C "$repo" log \
        --since="$SINCE" --until="$UNTIL" \
#        --author="$AUTHOR" --no-merges \
        --shortstat --pretty=format:"" \
        | awk '/files? changed/ {f+=$1; i+=$4; d+=$6} END {printf "files=%d insertions=%d deletions=%d\n", f, i, d}'
    fi

    echo ""
    echo '```'
    echo ""
  done
} > "$OUTPUT"

echo "wrote: $OUTPUT"
