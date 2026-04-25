---
name: next-task-suggester
description: Recommends today's next action across local repos by analyzing the last 5 git commits and pr_todo.md per repo, scoring recency/frequency/todo/interest/relevance with weighted signals, and ranking projects with reasoning tags (continue/resume/explore). Use when the user asks "what should I work on today", "다음에 뭐 할까", or wants daily standup planning across multiple projects.
---

# next-task-suggester

Scans the repos registered in `~/workspace/worklog/config.yaml` (and optionally any `~/workspace/*/.git`), caches each repo's last-5 git commits and pr_todo-style markdown into a local SQLite DB, computes weighted activity signals, and prints a ranked list of "what to work on next" with reasoning.

Built to mirror the layout of the `worklog-refiner` skill (3 docs + scripts dir, PEP 723 inline `uv run` shebangs).

## Files

- `SKILL.md` — this file. Purpose, entry points, contracts.
- `reference.md` — signal definitions, scoring formula, DB schema, future extensions. Load when editing logic or weights.
- `examples.md` — sample input data and rendered output. Load for quality review.
- `scripts/collect.py` — scans repos and upserts git/todo data into SQLite.
- `scripts/score.py` — pure-function scoring + ranking on top of SQLite rows.
- `scripts/suggest.py` — entry point: collects (when stale) then prints recommendations.

## Quick start

```bash
# Default: cache reused if < 1h old; otherwise re-collect, then rank.
uv run .claude/skills/next-task-suggester/scripts/suggest.py

# Force refresh and show top 10.
uv run .claude/skills/next-task-suggester/scripts/suggest.py --refresh --top 10

# Strategy presets (see reference.md).
uv run .../suggest.py --mode momentum   # weight Recency/Frequency
uv run .../suggest.py --mode balance    # weight TODO/Interest

# Structured output (for downstream tooling).
uv run .../suggest.py --json

# Collect only (cron / scheduled run).
uv run .claude/skills/next-task-suggester/scripts/collect.py
```

## Input contract

- `--config PATH` (default `~/workspace/worklog/config.yaml`) — same `repos:` list consumed by `daily_worklog.py`.
- `--discover` — additionally scan every `~/workspace/*/.git` directory.
- `--all-authors` — disable the default own-author filter.
- `--db PATH` — override SQLite location (default `${XDG_STATE_HOME:-~/.local/state}/next-task-suggester/cache.db`).
- `--cache-ttl SECONDS` — skip collect when last run is younger than this (default `3600`).

## Output contract

- Default: human-readable Markdown to stdout.
  - `# Today's suggestions (YYYY-MM-DD)` header
  - One `N. <repo>  [tag]` block per item with reasoning bullets and a `Score` line
- `--json`: a JSON array, one object per repo, with fields `rank`, `repo`, `project`, `tag`, `score`, `signals`, `reasons`.
- Exit code `0` always; warnings to stderr.

## When to invoke

Trigger phrases: "what should I work on today", "오늘 뭐 할까", "다음에 뭐 할까", "daily standup", "where did I leave off".

See `reference.md` for the formula and how to tune weights, and `examples.md` for the rendered output format.
