# 업무일지 + Obsidian 프로젝트 관리 런북

DAILY_LOG와 git commit으로 하루 업무일지를 만들고, 그 결과를 Obsidian 프로젝트 노트에 쌓는 흐름.

`worklogs/<date>.md` 한 파일에 두 독자가 있다.

| 구간 | 독자 | 내용 |
|---|---|---|
| `---` 위 (30줄) | 회사 업무일지 보고 | Context / 이슈사항 / 검토사항 / 작업완료 사항 |
| `---` 아래 | 나 — 작업관리, 이직·면접 재료 | `### Projects` 체크박스, `### Highlights`, `### Repositories` |

```
claude-mem ──(크론)──▶ DAILY_LOG/DAILY_LOG.md ──▶ 보고용 본문 (---  위)
git commit ──00_run_commit_worklog.py──────────────────────▶ Projects / Repositories (--- 아래)
                                         LLM ────▶ Highlights (--- 아래)
worklogs/<date>.md ──03_sync_obsidian.py──▶ astro-blog/01.Project/<project>/<project>_todo.md
```

## 매일 하는 일

Claude Code에서 `report-worklog` 스킬로 실행한다: "10월 8일 업무일지 써줘".
스킬이 아래 순서를 그대로 밟는다. 손으로 돌릴 때도 같은 순서다.

```bash
# 1. 해당 날짜 DAILY_LOG 항목 모으기
just report-sessions 2026-10-08         # 들어있는 블록 헤딩만 확인
just report-digest 2026-10-08           # 전체 digest

# 2. 보고용 본문 작성 (LLM) → worklogs/2026-10-08.md
sed '/^---$/,$d' worklogs/2026-10-08.md | sed '$d' | wc -l   # 30 이하

# 3. git commit 기반 워크로그를 본문 아래에 붙이기
OUTPUT=/tmp/worklog-2026-10-08.md ./00_run_commit_worklog.py 2026-10-08
printf '\n---\n\n' >> worklogs/2026-10-08.md
cat /tmp/worklog-2026-10-08.md >> worklogs/2026-10-08.md

# 4. ### Highlights 작성 (LLM) — ### Repositories 바로 위

# 5. Obsidian 반영
just obsidian-dry 2026-10-08            # diff만 확인
just obsidian-sync 2026-10-08
```

형식 규칙(본문 30줄, 불릿 모양, Highlights 규칙)은 `.claude/skills/report-worklog/reference.md`.

### 같은 날짜를 다시 쓸 때

- 본문만 고칠 때: `---` 위만 수정 → `just obsidian-sync <date>`
- 커밋 워크로그까지 다시 만들 때: `---` 앞 빈 줄부터 끝까지 지우고 3단계부터
- 그 날짜로 Jira 이슈를 이미 만들었다면 `---` 아래는 다시 만들지 않는다 (이슈 링크 key가 바뀐다)

## Highlights 쓰는 법

이직·면접 때 꺼내 쓸 재료. 프로젝트별로 결과가 있는 것만, 1~3줄.

```markdown
### Highlights

#### data-orchestrator
- <문제/상황> — <조치(기술·판단)> — <결과(수치)>
```

- `####` 이름은 `### Projects`의 프로젝트명(repo 폴더명)과 같아야 노트로 넘어간다
- 기술명은 그대로 쓴다 (ClickHouse, Loki, sqlglot …)
- 수치는 DAILY_LOG에 있는 것만. 수치가 없으면 쓰지 않는다
- 원인 규명, before→after 측정, 기각한 안과 그 근거가 가장 좋은 재료

## Obsidian 노트에 쌓이는 모양

`~/workspace/astro-blog/01.Project/<project>/<project>_todo.md`의 `## Worklog` 섹션:

```markdown
%% worklog:auto:start date=2026-10-08 project=data-orchestrator %%
- [x] ... #project/data-orchestrator #todo #feat [due:: 2026-10-08] [completion:: 2026-10-08]
- <Highlights 줄>
%% worklog:auto:end %%
```

- 날짜별 블록이 아래로 쌓인다. 같은 날짜를 다시 반영하면 그 블록만 교체된다
- 블록 밖에 직접 쓴 내용(Goal, Reference 등)은 건드리지 않는다
- 노트가 없으면 `01.Project/workspace/workspace_todo.md` 템플릿 구성으로 만든다
  (frontmatter `status: IN_PROGRESS`, `tag: project/<name>`, dataview 블록 포함)
- `- [x]` 항목은 Goal 섹션의 dataview 표에 완료로, `- [ ]`는 진행중으로 잡힌다

## 프로젝트 목록 관리

### 수집 대상 repo

`config.yaml`의 `repos`에 있는 repo의 커밋만 워크로그에 잡힌다. 프로젝트명은 repo 폴더명.

```yaml
repos:
  - path: ~/workspace/data-integration
    group: data-engineering      # Jira Epic 배정에 쓰인다. 개인 repo는 worklog-system
```

- 새 repo를 쓰기 시작하면 여기에 추가한다
- 같은 저장소를 두 번 클론한 폴더(예: `tkc-pipeline`은 cdp-pipeline 클론)는 커밋이 중복되므로 넣지 않는다

### 01.Project 정리 기준

- 최근 90일 내 내 커밋(또는 vault 노트 수정)이 있으면 `01.Project`에 둔다
- 90일 넘게 기록이 없으면 `04.Archive/archived_project/`로 옮긴다

repo별 마지막 커밋 날짜:

```bash
cd ~/workspace && for d in */ dagster/*/; do
  [ -e "$d/.git" ] && echo "$(git -C "$d" log --all -1 --format=%cs --author=jinheonyoon) $d"
done | sort -r
```

## 자주 막히는 곳

**`no DAILY_LOG section found for <date>`**
그 날짜 항목이 DAILY_LOG에 없다. 크론(`~/.hermes/scripts/daily_log_update.sh` → `scripts/daily_log_update.py`)이
돌았는지, `scripts/.daily_log_update.ckpt` 시각이 최근인지 확인한다.

**`00_run_commit_worklog.py`가 exit 2로 끝났다**
LLM 백엔드가 모두 실패해 raw를 그대로 저장한 것이다. `/tmp/refine-<backend>.err`를 보고
`--backend gemini` 등으로 다시 돌린다. `---` 아래를 손으로 쓰지 않는다.

**프로젝트 노트에 반영이 안 된다**
`### Projects`의 `####` 이름과 프로젝트 폴더명이 다르거나, 그 repo가 `config.yaml`에 없다.
`just obsidian-dry <date>`로 어떤 노트가 바뀌는지 먼저 본다.

**repo 루트에 `<date>.md`가 생긴다**
`00_run_commit_worklog.py`를 `OUTPUT` 없이 돌린 것이다. 기본 출력이 repo 루트다.

## 관련 파일

| 파일 | 역할 |
|---|---|
| `scripts/daily_log_update.py` | claude-mem 기록 → `DAILY_LOG/DAILY_LOG.md` (크론) |
| `.claude/skills/report-worklog/` | 전체 흐름 스킬, 형식 규칙, `scripts/extract_sections.py`(digest 추출) |
| `00_run_commit_worklog.py` | git log 추출 + LLM 정리 오케스트레이션 |
| `01_extract_git_raw.py` | `config.yaml` repo들의 git log → raw 마크다운 |
| `02_refine_commit_worklog.py` | raw → `### Projects` 형식 (프롬프트: `.claude/skills/worklog-refiner/`) |
| `03_sync_obsidian.py` | Projects·Highlights → Obsidian 프로젝트 노트 |
| `config.yaml` | 수집 repo 목록, group, Jira 설정 |
| `archive/` | 더 이상 쓰지 않는 도구·예전 출력 |

Jira 이슈 생성은 `docs/worklog-jira-runbook.md` 참조 — 같은 파일의 `### Projects`만 읽는다.
