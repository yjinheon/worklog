# report-worklog — reference

Format spec for `worklogs/<date>.md`. Budget: **30 lines including blanks.**

## Skeleton

```
YYYY-MM-DD
                    <- blank
Context
                    <- blank
<one sentence>
                    <- blank
이슈사항
                    <- blank
- ... (5~7)
                    <- blank
검토사항
                    <- blank
- ... (3~4)
                    <- blank
작업완료 사항
                    <- blank
- ... (6~8)
```

Section headings are bare text — no `#`, no `**`.

## Line budget

| Section | Bullets | Lines |
|---|---|---|
| date + Context | — | 5 |
| 이슈사항 | 5~7 | 7~9 |
| 검토사항 | 3~4 | 5~6 |
| 작업완료 사항 | 6~8 | 8~10 |

Over budget → merge two bullets into one with a `·`-joined clause. Never delete a
section, and never truncate mid-thought.

## Bullet shape

```
- <핵심 판단·결과> — <근거·수치·증상>
```

- 개조식, **명사형 종결**: `…철회`, `…수정`, `…재분류`, `…축소`. Never `…했다`, `…한다`.
- One bullet = one 단위업무. If two clauses share no cause, they are two bullets.
- Em-dash tail is optional but preferred: it carries the evidence that makes the
  head credible.

## Section rules

### Context

One prose sentence (서술형 허용, this line only). Names the repos touched and the
day's shape. Multi-session days get one sentence covering all sessions, ordered
by session number — not one sentence per session.

### 이슈사항

Problems that existed or surfaced today. Group by **cause**, not by symptom:

- Symptoms sharing one root → one bullet, symptoms listed after the em-dash.
  (2026-08-28: DLQ 유실 · 커서 무한 루프 · KST 미변환 → one bullet, "통합하자 노출된 결함 3건".)
- Two settings problems in the same config surface → one bullet.
- A problem already fixed today still belongs here; the fix goes in 작업완료 사항.

Exclude: personal lessons (`이번 세션에서 얻은 함정`), backlog items (`남은 것 · 주의`),
and tooling friction that changed no production behavior.

### 검토사항

Only items that reached a **verdict**: 기각 / 철회 / 보류 / 반대 / 재분류 / 격상.
Each bullet must state the verdict *and* the evidence that produced it.
"검토했다"로 끝나고 결론이 없으면 넣지 않는다.

A rejected proposal is worth more lines than an accepted one — record why it was
rejected so the same proposal does not return.

### 작업완료 사항

Shipped work, in dependency order (구현 → 검증 → 배포 → 후속 판별), then refactoring
tracks. Keep every measured number:

- diff size (`9파일 +516/−254`), test counts (`2,984 passed / 0 failed`)
- before→after reductions (`593→87줄`), scope counts (`29개 테이블`, `호출부 15곳`)
- release identity checks (`4개 식별자 일치`, `import error 0`)

Merge the smallest track into an adjacent bullet before exceeding the budget.

## Excluded from output

| DAILY_LOG block | Why |
|---|---|
| `### Resume / Portfolio 반영 초안` | Separate audience; stripped by the extractor |
| `### 이번 세션에서 얻은 함정` | Personal retrospective, not a report item |
| `### 남은 것 · 주의` | Backlog; belongs in the next day's input |
| 요약 tables | Re-derived as bullets; tables blow the line budget |

## Work detail (`/tmp/work-<date>.md` → daily note Work block)

The **first** output of the flow: the full work record for the day, written
before the company worklog. Same four bare-text headings as the report body
(`Context` / `이슈사항` / `검토사항` / `작업완료 사항`), then an optional
`### Highlights` (see § Highlights).

- **No line or bullet budget.** Keep every measured number, rationale, and
  verdict the digest contains.
- **Personal repos included** (`worklog-system` group: lazyflow, local-flow,
  my-scripts, worklog, …). The company worklog is the subset of this block
  with those dropped.
- Same bullet shape and grouping rules as the report body.
- Plain `- ` bullets under `작업완료 사항` are stored as-is; only the
  commit worklog's `### Projects` checkboxes become task lines.
- The company worklog body (`---` 위) must be a **subset**: every company
  bullet must appear (possibly reworded within the same 단위업무) in the Work
  block.

## Verify

```bash
sed '/^---$/,$d' worklogs/<date>.md | sed '$d' | wc -l  # <= 30 (report body; drops the blank before ---)
sed '/^---$/,$d' worklogs/<date>.md | grep -c '^- '     # 14 ~ 19
grep -c '^### Projects' worklogs/<date>.md          # 1 (appended commit worklog)
```

## Appended commit worklog

Below the report, after a `---` line, `00_run_commit_worklog.py` output is appended verbatim
(`## <date>` · `### Summary` · `### Projects` · `### Repositories`). Not counted
toward the 30-line budget, not reworded. `### Projects` is what
`03_sync_obsidian.py` and `jira_create.py` read.

Two audiences: the report body is the company 업무일지; everything below `---`
is personal — task tracking in Obsidian and raw material for 이직·면접.

## Highlights

Written by hand (by the skill) into **`/tmp/work-<date>.md`** — the daily
note Work detail — as a `### Highlights` section after `작업완료 사항`.
`03_sync_obsidian.py` retags each bullet to `- #highlight #project/<repo>`
and stores it in the daily note's `## 03. Work` block. Highlights are **not**
written to the company worklog file and not to any `01.Project` note
(project notes read them via a dataview query on `#highlight`).

```
### Highlights

#### <repo folder name>
- <문제/상황> — <조치(기술·판단)> — <결과(수치)>
```

- Only projects with a result worth telling in an interview; 1~3 bullets each, 0 is fine.
- Unlike the report body, keep technology names (ClickHouse, Loki, sqlglot …) —
  they are the searchable part of a resume.
- Every number comes from DAILY_LOG; no number → no bullet. Root-cause finds,
  before→after measurements and rejected approaches are the best material.
