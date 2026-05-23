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
    ./scripts/plan.sh {{top}}

# 9) worklog를 Obsidian daily note의 Today 섹션에 반영
obsidian-sync date="":
    ./sync_worklog_to_obsidian.py {{date}}

# 10) Obsidian 반영 diff만 확인
obsidian-dry date="":
    ./sync_worklog_to_obsidian.py {{date}} --dry-run

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
