script := ".claude/skills/next-task-suggester/scripts"
db := "~/.local/state/next-task-suggester/cache.db"

default:
    @just --list

# 1) 수집만 실행 — DB가 만들어지는지 확인
collect:
    uv run {{script}}/collect.py

# 2) 추천 출력 (캐시 사용)
suggest:
    uv run {{script}}/suggest.py

# 3) 강제 새로고침 + top N (기본 10)
refresh top="10":
    uv run {{script}}/suggest.py --refresh --top {{top}}

# 4) 모드별 추천
balance:
    uv run {{script}}/suggest.py --mode balance

momentum:
    uv run {{script}}/suggest.py --mode momentum

# 5) JSON 출력
json top="3":
    uv run {{script}}/suggest.py --json --top {{top}}

# 6) 등록 안 된 repo도 자동 발견
discover:
    uv run {{script}}/suggest.py --discover --refresh

# 7) 본인 외 author 커밋도 포함
all-authors:
    uv run {{script}}/suggest.py --all-authors --refresh

# 8) 추천 + Claude(sonnet-4.6)로 이유/오늘 할일 요약 (commit + project 기반)
plan top="3":
    #!/usr/bin/env bash
    set -euo pipefail
    JSON=$(uv run {{script}}/suggest.py --json --top {{top}} | grep -v '^#')
    REPOS=$(echo "$JSON" | python3 -c "import json,sys;[print(r['repo']) for r in json.load(sys.stdin)]")
    {
      echo "# 추천 후보 (JSON)"
      echo "$JSON"
      echo
      echo "# 후보별 최근 커밋 (최신 5개)"
      for r in $REPOS; do
        echo
        echo "[$r]"
        sqlite3 {{db}} "SELECT '  - '||subject FROM commit_log WHERE repo='$r' ORDER BY ts DESC LIMIT 5;"
      done
    } | claude -p --model claude-sonnet-4-6 \
        "각 후보별로 (1) 왜 오늘 이 프로젝트를 진행하면 좋은지, (2) 오늘 할 작업 1-2개를 commit message와 project 정보 근거로 한국어 2-3줄씩만 짧게 요약해."

# SQLite: 등록된 repo 보기
db-repos:
    sqlite3 {{db}} "SELECT name, project, branch FROM repo;"

# SQLite: repo별 커밋 수와 마지막 커밋 시간
db-commits:
    sqlite3 {{db}} "SELECT repo, COUNT(*) AS commits, datetime(MAX(ts),'unixepoch','localtime') AS last FROM commit_log GROUP BY repo ORDER BY last DESC;"

# SQLite: TODO 적재 결과
db-todos:
    sqlite3 {{db}} "SELECT repo, file, open_count, done_count FROM todo;"

# SQLite: 최근 collect 이력
db-runs:
    sqlite3 {{db}} "SELECT datetime(ts,'unixepoch','localtime') AS at, repo_count, ok_count, new_commit_repos FROM collect_run ORDER BY ts DESC LIMIT 10;"

# DB 초기화 — 다음 호출에서 새로 만들어짐
db-reset:
    rm -f {{db}}
