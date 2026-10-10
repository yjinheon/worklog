---
name: report-worklog
description: Turn every DAILY_LOG.md session section for one date into a concise report-style Korean worklog (worklogs/YYYY-MM-DD.md) — Context / 이슈사항 / 검토사항 / 작업완료 사항, 개조식 명사형 bullets, 30 lines max — then appends the 00_run_commit_worklog.py commit worklog and syncs its project checkboxes to Obsidian 01.Project todos. Use when asked to write 오늘자/특정일 업무일지 for reporting (보고용), not for the Jira-facing bullet format.
---

# report-worklog

Writes the **보고용** daily worklog: the reader is a manager or teammate scanning
for what broke, what was decided, and what shipped — not a portfolio reviewer and
not the Jira importer.

Source is `DAILY_LOG/DAILY_LOG.md`, which holds one `## <date>` heading **per work
session**, so one day usually spans several sections. The output is a single
merged file at `worklogs/<date>.md`.

## Files

- `SKILL.md` — this file. Workflow and hard constraints.
- `reference.md` — section-by-section format rules, grouping rules, what to drop. Load before writing.
- `examples.md` — before/after from 2026-08-28 (raw sections → final worklog), plus a 압축 전/후 pair. Load for quality calibration.
- `scripts/extract_sections.py` — deterministic digest builder (no LLM).

## Workflow

1. **Extract** — build the digest for the date:
   ```bash
   python .claude/skills/report-worklog/scripts/extract_sections.py 2026-08-28
   ```
   Sections are merged in chronological order (세션 1 → N) and the
   `Resume / Portfolio 반영 초안` blocks are stripped (they are a separate output).
2. **Calibrate** — read the previous day's `worklogs/<date-1>.md` so wording and
   granularity stay consistent across the series.
3. **Write** `worklogs/<date>.md` per `reference.md`.
4. **Verify** — report body (above the `---` line) must be ≤ 30 lines. If over, merge bullets; never truncate a section.
5. **Append commit worklog** — run the git-commit refiner and append its output
   below a `---` separator (verbatim, no rewording):
   ```bash
   OUTPUT=/tmp/worklog-<date>.md ./00_run_commit_worklog.py <date>
   printf '\n---\n\n' >> worklogs/<date>.md && cat /tmp/worklog-<date>.md >> worklogs/<date>.md
   ```
   On rewrite, delete the blank line before `---` and everything below first so it is never appended twice.
   If the refiner fails (exit ≠ 0 / raw fallback), skip the append and report it — do not hand-write it.
   Then insert a `### Highlights` section just before `### Repositories` — see
   `reference.md` § Highlights. This is the 작업관리·이직/면접 재료 layer, written for
   *me*, not for the report reader.
6. **Sync Obsidian** — push the appended `### Projects` checkboxes and `### Highlights` into
   `~/workspace/astro-blog/01.Project/<project>/<project>_todo.md` (one auto block per date;
   missing project folders are created from the project template):
   ```bash
   ./03_sync_obsidian.py <date> --skip-run-worklog --worklog-path worklogs/<date>.md
   ```

## Hard constraints

- **30 lines max** for the report body, blank lines included. The appended commit
  worklog below `---` is generated output and not counted.
- Four sections, in order: date line → `Context` → `이슈사항` → `검토사항` → `작업완료 사항`.
- Korean, 개조식 **명사형 종결**. Bullet shape: `핵심 판단·결과 — 근거·수치`.
  Only the `Context` line is a full sentence.
- Group by **단위업무**, not by symptom. Individual symptoms belong after the em-dash
  of the bullet they support.
- Keep measured numbers (line deltas, test counts, table counts). Drop adjectives.
- Never invent facts absent from DAILY_LOG. An unmeasured claim is dropped, not softened.

## Not this skill

- Jira issue creation (`jira_create.py`) ignores the 개조식 report body; it reads the
  appended `### Projects` checkboxes (`- [x] … #project/<name>`).
- Resume/portfolio phrasing lives in the DAILY_LOG's own `Resume / Portfolio 반영 초안`
  blocks and is deliberately stripped here.

See `reference.md` for the full format spec.
