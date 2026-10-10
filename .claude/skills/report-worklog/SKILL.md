---
name: report-worklog
description: Turn every DAILY_LOG.md session section for one date into the Obsidian daily note Work block (detailed, no line limit, personal repos included) with task lines and Highlights, then distill a 30-line company worklog (worklogs/YYYY-MM-DD.md) from that Work block and append the commit worklog. Use when asked to write 오늘자/특정일 업무일지 for reporting (보고용), not for the Jira-facing bullet format.
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

1. **Extract** — build the digest and the commit worklog for the date:
   ```bash
   just report-digest 2026-10-08            # DAILY_LOG digest (input)
   OUTPUT=/tmp/worklog-2026-10-08.md ./00_run_commit_worklog.py 2026-10-08
   ```
2. **Work detail (daily-log first)** — the source of truth is the Obsidian
   daily note's `## 03. Work` block, **not** the company worklog. Calibrate
   against the previous day's `worklogs/<date-1>.md`, then write
   `/tmp/work-<date>.md` in the same four headings as the company worklog
   (`Context` / `이슈사항` / `검토사항` / `작업완료 사항`) with **no line or
   bullet limit, personal repos included, every number and rationale kept**.
   Append a `### Highlights` section (`#### <repo>` subheads + `- ` bullets)
   — see `reference.md` § Highlights.
3. **Sync daily note** — push the Work detail and the task lines:
   ```bash
   ./03_sync_obsidian.py <date> --skip-run-worklog \
     --worklog-path /tmp/work-<date>.md \
     --commit-worklog /tmp/worklog-<date>.md
   ```
   Writes the Work detail into the daily note's `## 03. Work` report block
   and the commit-based task lines into `### Today`. It never writes into
   existing `01.Project` notes (only creates a missing project note from
   the template).
4. **Company worklog (subset)** — pick **only the company work** out of the
   daily note's Work block (drop `worklog-system` personal-repo items),
   compress to 30 lines per `reference.md`, and write `worklogs/<date>.md`.
5. **Append commit worklog** — below a `---` separator, verbatim, no
   rewording (this is the part Jira reads):
   ```bash
   printf '\n---\n\n' >> worklogs/<date>.md && cat /tmp/worklog-<date>.md >> worklogs/<date>.md
   ```
   On rewrite, delete the blank line before `---` and everything below first
   so it is never appended twice. If the refiner fails (exit ≠ 0 / raw
   fallback), skip the append and report it — do not hand-write it.
6. **Verify** — report body (above `---`) ≤ 30 lines, and every company
   bullet in the body appears in the daily note's Work block
   (daily-log ⊇ company worklog).

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
