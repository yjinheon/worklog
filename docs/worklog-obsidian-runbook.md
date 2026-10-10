# 업무일지 + Obsidian daily note 런북

DAILY_LOG와 git commit으로 하루 업무를 만들고, **Obsidian daily note를 먼저** 쓰고,
그에서 회사 업무일지를 잘라내는 흐름.

daily note가 원본이고, 회사 업무일지(`worklogs/<date>.md`)는 그 부분집합이다.

| 산출물 | 위치 | 내용 |
|---|---|---|
| Work 상세 | `02.Area/daily-log/<date>.md` → `## 03. Work` report 블록 | 4 제목(Context / 이슈사항 / 검토사항 / 작업완료 사항) + `### Highlights` — 줄 제한 없음, 개인 repo 포함, 수치·근거 전부 |
| 작업 줄 | 같은 노트 → `### Today` auto 블록 | 커밋 워크로그 `### Projects` 체크박스에서 파생, `- [x] … #project/P #todo #type [due:: D] [completion:: D]` |
| 회사 업무일지 | `worklogs/<date>.md` | Work 상세에서 회사 업무만 골라 30줄로 압축 + `---` 아래 커밋 워크로그(Jira가 읽는 `### Projects`) |

```
DAILY_LOG ──extract_sections.py──▶ digest ──(LLM)──▶ /tmp/work-<date>.md (Work 상세)
git commit ──00_run_commit_worklog.py──▶ /tmp/worklog-<date>.md (Projects·Repositories)
                                          │
03_sync_obsidian.py ◀─────────────────────┘
   ├─▶ daily note ## 03. Work report 블록  (Work 상세)
   ├─▶ daily note ### Today auto 블록       (작업 줄)
   └─▶ 01.Project: 새 프로젝트 노트 생성만 (존재하는 노트에는 쓰지 않음)

Work 상세 ──(LLM)──▶ worklogs/<date>.md (회사 업무만 30줄)
                + printf '\n---\n\n' + /tmp/worklog-<date>.md (Jira용)
```

## 매일 하는 일

Claude Code에서 `report-worklog` 스킬로 실행한다: "10월 8일 업무일지 써줘".
스킬이 아래 순서를 그대로 밟는다. 손으로 돌릴 때도 같은 순서다.

```bash
# 1. digest + 커밋 워크로그
just report-digest 2026-10-08
OUTPUT=/tmp/worklog-2026-10-08.md ./00_run_commit_worklog.py 2026-10-08

# 2. Work 상세 작성 (LLM) → /tmp/work-2026-10-08.md
#    - 4 제목, 줄 제한 없음, 개인 repo 포함, 수치·근거 전부
#    - 끝에 ### Highlights (#### <repo> + - 불릿)

# 3. daily note 반영
just obsidian-dry 2026-10-08            # diff만 확인
just obsidian-sync 2026-10-08           # Work 블록 + Today 작업 줄

# 4. 회사 업무일지 (LLM) — daily note Work 블록에서 회사 업무만 골라 30줄 압축
#    → worklogs/2026-10-08.md
sed '/^---$/,$d' worklogs/2026-10-08.md | sed '$d' | wc -l   # 30 이하

# 5. 커밋 워크로그 붙이기 (Jira가 읽는 ### Projects)
printf '\n---\n\n' >> worklogs/2026-10-08.md
cat /tmp/worklog-2026-10-08.md >> worklogs/2026-10-08.md
```

형식 규칙(Work 상세, 회사 일지 30줄, 불릿 모양, Highlights 규칙)은
`.claude/skills/report-worklog/reference.md`.

### 같은 날짜를 다시 쓸 때

- Work 상세만 고칠 때: `/tmp/work-<date>.md` 수정 → `just obsidian-sync <date>`
  (같은 날짜 report 블록만 교체, 멱등)
- 회사 일지 본문만 고칠 때: `worklogs/<date>.md` `---` 위만 수정 (Obsidian 반영 불필요)
- 커밋 워크로그까지 다시 만들 때: `worklogs/<date>.md` `---` 앞 빈 줄부터 끝까지 지우고 5단계부터
- 그 날짜로 Jira 이슈를 이미 만들었다면 `---` 아래는 다시 만들지 않는다 (이슈 key가 바뀐다)

## daily note에 쌓이는 모양

`02.Area/daily-log/<date>.md` (기존 노트가 하위 폴더 `2026/2026-MM/`에 있으면 그 파일을
고치고, 없으면 `02.Area/daily-log/<date>.md`에 vault 템플릿으로 새로 생성):

```markdown
### Today

**오늘 작성한  할일**

%% worklog:auto:start date=2026-10-08 %%
- [x] TMA 캠페인 유저 인입 RT … #project/data-orchestrator #todo #perf [due:: 2026-10-08] [completion:: 2026-10-08]
%% worklog:auto:end %%

## 03. Work

%% worklog:report:start date=2026-10-08 %%
<Work 상세 — 4 제목 + ### Highlights(#highlight #project/X 태그)>
%% worklog:report:end %%
```

- 같은 날짜를 다시 반영하면 그 블록만 교체된다 (멱등, 2번째는 `no changes`)
- report 블록 안의 `## ` 헤딩이 있어도 블록 경계는 마커(`%%`)로만 판별한다
- 블록 밖에 사용자가 쓴 내용(TODO, Track, Thoughts 등)은 건드리지 않는다

## 01.Project 노트

작업 줄·Highlights를 **복사하지 않는다** — dataview 쿼리로 daily note를 본다.

- `## Goal` 표: `#project/P` 태그의 task 중 완료/진행중 (query: `SORT T.completion DESC`)
- `## Worklog`: `#highlight AND #project/P` 불릿 조회 (Highlights는 daily note에만 존재)
- `03_sync_obsidian.py`가 01.Project에 쓰는 경우는 단 하나:
  `config.yaml` repo에 있고 노트가 없는 프로젝트의 노트를 템플릿으로 생성
  (`04.Archive/archived_project/`에 있는 프로젝트는 재생성하지 않음)

## Highlights 쓰는 법

이직·면접 때 꺼내 쓸 재료. 프로젝트별로 결과가 있는 것만, 1~3줄.

```markdown
### Highlights

#### data-orchestrator
- <문제/상황> — <조치(기술·판단)> — <결과(수치)>
```

- `####` 이름은 repo 폴더명(`#project/` 태그와 동일)
- 기술명은 그대로 쓴다 (ClickHouse, Loki, sqlglot …)
- 수치는 DAILY_LOG에 있는 것만. 수치가 없으면 쓰지 않는다
- 원인 규명, before→after 측정, 기각한 안과 그 근거가 가장 좋은 재료
- 회사 일지 파일에도, 01.Project 노트에도 쓰지 않는다 — daily note Work 블록에만

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
- LLM이 옛 커밋 메시지에서 지어낸 프로젝트명(`#project/data-pipeline-refactor` 등)은
  작업 줄·Highlights에 그대로 남지만 프로젝트 노트 생성 대상이 아니다 (config repo만 생성)

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
`--backend claude` 등으로 다시 돌린다. auto 순서는 litellm → claude → gemini → kimi → codex.
`---` 아래를 손으로 쓰지 않는다.

**daily note에 작업 줄이 안 잡힌다**
커밋 워크로그 `### Projects`의 `####` 이름과 repo 폴더명이 다르거나, 그 repo가 `config.yaml`에
없다. `just obsidian-dry <date>`로 어떤 노트가 바뀌는지 먼저 본다.
Tasks 플러그인의 전역 필터가 `#todo`이므로 `#todo` 태그 없는 체크박스는 작업 목록에 안 뜬다.

**repo 루트에 `<date>.md`가 생긴다**
`00_run_commit_worklog.py`를 `OUTPUT` 없이 돌린 것이다. 기본 출력이 repo 루트다.

## 관련 파일

| 파일 | 역할 |
|---|---|
| `scripts/daily_log_update.py` | claude-mem 기록 → `DAILY_LOG/DAILY_LOG.md` (크론) |
| `.claude/skills/report-worklog/` | 전체 흐름 스킬, 형식 규칙, `scripts/extract_sections.py`(digest 추출) |
| `00_run_commit_worklog.py` | git log 추출 + LLM 정리 오케스트레이션 |
| `01_extract_git_raw.py` | `config.yaml` repo들의 git log → raw 마크다운 |
| `02_refine_commit_worklog.py` | raw → `### Projects` 형식 (백엔드: litellm/claude/gemini/kimi/codex) |
| `03_sync_obsidian.py` | Work 상세 → daily note report 블록, 작업 줄 → Today, 새 프로젝트 노트 생성만 |
| `worklog_parser.py` | `worklogs/<date>.md` / 커밋 워크로그 → 작업 줄 items |
| `config.yaml` | 수집 repo 목록, group, Jira 설정 |
| `archive/` | 더 이상 쓰지 않는 도구·예전 출력 |

Jira 이슈 생성은 `docs/worklog-jira-runbook.md` 참조 — 같은 파일의 `### Projects`만 읽는다.
