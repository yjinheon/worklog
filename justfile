default:
    @just --list

# 1) 보고용 업무일지 원본 추출 (DAILY_LOG의 해당 날짜 항목 전부 병합)
report-digest date="":
    uv run .claude/skills/report-worklog/scripts/extract_sections.py {{date}}

# 2) 해당 날짜의 DAILY_LOG 섹션/업데이트 블록 헤딩만 확인
report-sessions date="":
    uv run .claude/skills/report-worklog/scripts/extract_sections.py {{date}} --list

# 3) git 커밋 기반 worklog 생성 (OUTPUT=경로 로 출력 위치 지정)
commit-log date="":
    ./00_run_commit_worklog.py {{date}}

# 4) Work 상세(/tmp/work-DATE.md) + 커밋 워크로그 → daily note 반영 (daily-log 먼저)
obsidian-sync date:
    ./03_sync_obsidian.py {{date}} --skip-run-worklog --worklog-path /tmp/work-{{date}}.md --commit-worklog /tmp/worklog-{{date}}.md

# 5) Obsidian 반영 diff만 확인
obsidian-dry date:
    ./03_sync_obsidian.py {{date}} --skip-run-worklog --worklog-path /tmp/work-{{date}}.md --commit-worklog /tmp/worklog-{{date}}.md --dry-run

# 6) 워크로그 -> Jira 이슈 생성 계획만 출력 (dry-run)
jira-plan start end:
    ./jira_create.py --start {{start}} --end {{end}}

# 7) 편집한 계획 파일을 적용 (권장 경로)
jira-from-plan plan:
    ./jira_create.py --apply --from-plan {{plan}}

# 8) 계획 확인 없이 바로 생성 (확인 프롬프트 있음)
jira-apply start end:
    ./jira_create.py --start {{start}} --end {{end}} --apply

# 9) 완료 이슈 CSV 내보내기
jira-done start end:
    ./jira_done.py --start {{start}} --end {{end}}
