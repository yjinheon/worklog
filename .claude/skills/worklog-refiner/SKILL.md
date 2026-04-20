---
name: worklog-refiner
description: Build the LLM prompt that converts a raw multi-repo git-commit digest into a standardized Korean daily worklog (Markdown). Use when invoking Claude or Gemini to transform `/tmp/worklog-YYYY-MM-DD.raw.md` into `YYYY-MM-DD.md`.
---

# worklog-refiner

Assembles the prompt sent to an LLM to refine a daily `raw.md` (aggregated git commits from multiple repos) into a standardized worklog.

Consumed by `refine_worklog.py`, which calls Claude (subscription OAuth) first and falls back to Gemini on failure.

## Files

- `SKILL.md` — this file. Purpose and usage summary.
- `reference.md` — output format rules, Types whitelist, classification rules. Load when editing the prompt.
- `examples.md` — before/after examples (raw input vs. refined output). Load for quality review.
- `scripts/build_prompt.py` — utility that assembles the prompt and writes it to stdout.

## Quick start

```bash
# Standalone: print the assembled prompt to stdout
python .claude/skills/worklog-refiner/scripts/build_prompt.py \
    --input /tmp/worklog-2026-04-20.raw.md \
    --vocab ~/workspace/worklog/vocab.yaml

# Imported from refine_worklog.py
from build_prompt import build_prompt
prompt = build_prompt(input_path=..., vocab_path=...)
```

## Input contract

- `input_path` (required): raw markdown file produced by `daily_worklog.py`, typically `/tmp/worklog-<date>.raw.md`.
- `vocab_path` (optional): YAML containing `projects`, `repo_mapping`, `task_tags`, `system_tags`. Omit if not available — the prompt degrades gracefully.

## Output contract

- Writes the fully assembled prompt to stdout.
- The LLM MUST return **pure Markdown only**, MUST NOT wrap the whole document in a code fence, and MUST write in Korean.

See `reference.md` for the full format, Types whitelist, and classification rules.
