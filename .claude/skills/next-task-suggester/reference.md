# next-task-suggester — reference

Signal definitions, scoring formula, SQLite schema, and extension roadmap. Load this file when editing the scorer or adding new signals.

## Contents
- Signals (R, F, T, I, Rel)
- Scoring formula and mode presets
- Reasoning tags (continue / resume / explore)
- SQLite schema
- TODO file matching rules
- Author resolution
- Future extensions

## Signals

All signals are normalized to `[0, 1]`.

### R — Recency
Time since the most recent own-author commit.

```
hours = (now - last_commit_ts) / 3600
R = exp(-hours / 72)        # half-life 72h ≈ 3 days
```

- 0h → 1.0
- 72h → 0.37
- 1 week → 0.10
- No commit at all → 0.0

### F — Frequency
Engagement intensity within the last 5 commits.

```
spans = differences between consecutive commit_ts (seconds)
avg_span = mean(spans)         # only commits within last 30 days are counted
F = clamp(86400 / avg_span, 0, 1)   # one commit/day → 1.0
```

If fewer than 2 commits in the window: `F = 0.0`.

### T — TODO presence
Open `- [ ]` checkboxes across all matched markdown files in the repo.

```
T = min(1.0, open_count / 5)
```

If the repo has no TODO file at all: `T = 0.0`.

### I — Interest (implicit)
Share of the last `N=5` `collect_run` rows in which this repo produced new commits.

```
I = new_commit_runs / N
```

Bootstrap: when fewer than `N` runs exist, divide by the actual run count (so a single fresh repo can score `1.0`).

### Rel — Relevance
Jaccard similarity between the repo's token bag and the most-active repo's token bag.

```
tokens(repo) = lowercase tokens from {repo_name, last 5 commit subjects}
              minus stopwords (common verbs / punctuation)
Rel = |A ∩ B| / |A ∪ B|       (0 if denominator is 0)
```

The reference repo for similarity is the highest-`R` repo other than the candidate itself.

## Scoring formula

Default (mode = `default`):

```
Score = 0.35·R + 0.20·F + 0.25·T + 0.10·I + 0.10·Rel
```

Mode presets:

| mode       | R    | F    | T    | I    | Rel  | When to use                          |
|------------|------|------|------|------|------|--------------------------------------|
| `default`  | 0.35 | 0.20 | 0.25 | 0.10 | 0.10 | Balanced everyday recommendation     |
| `momentum` | 0.50 | 0.30 | 0.10 | 0.05 | 0.05 | Stay in flow on actively worked code |
| `balance`  | 0.15 | 0.10 | 0.40 | 0.20 | 0.15 | Prevent stagnation; surface neglected work |

Final score is rounded to 3 decimals.

## Reasoning tags

Exactly one tag per item, decided in this order:

1. `continue` — last commit within 24h.
2. `resume`   — open TODO ≥ 3 AND last commit older than 72h (or never).
3. `explore`  — `Rel ≥ 0.20` AND `R < 0.20` (related to active work but you haven't touched it lately).
4. otherwise no tag.

## SQLite schema

DB path: `${XDG_STATE_HOME:-~/.local/state}/next-task-suggester/cache.db`. Override with `--db PATH`.

```sql
CREATE TABLE IF NOT EXISTS repo (
  name        TEXT PRIMARY KEY,
  path        TEXT NOT NULL,
  project     TEXT,
  branch      TEXT,
  last_seen   INTEGER NOT NULL          -- collect epoch
);

CREATE TABLE IF NOT EXISTS commit_log (
  repo        TEXT NOT NULL,
  sha         TEXT NOT NULL,
  ts          INTEGER NOT NULL,         -- author date epoch
  author      TEXT,
  subject     TEXT NOT NULL,
  PRIMARY KEY (repo, sha)
);
CREATE INDEX IF NOT EXISTS idx_commit_repo_ts
  ON commit_log(repo, ts DESC);

CREATE TABLE IF NOT EXISTS todo (
  repo         TEXT NOT NULL,
  file         TEXT NOT NULL,           -- relative to repo root
  open_count   INTEGER NOT NULL,
  done_count   INTEGER NOT NULL,
  total_lines  INTEGER NOT NULL,
  sample       TEXT,                    -- first 3 open lines, '\n'-joined
  PRIMARY KEY (repo, file)
);

CREATE TABLE IF NOT EXISTS collect_run (
  ts                INTEGER PRIMARY KEY,
  repo_count        INTEGER NOT NULL,
  ok_count          INTEGER NOT NULL,
  new_commit_repos  TEXT                -- ',' joined repo names
);
```

Write policy:
- `repo`: upsert by `name` every collect.
- `commit_log`: `INSERT OR REPLACE` per `(repo, sha)`. Never delete — history grows.
- `todo`: `DELETE FROM todo WHERE repo=?` then re-insert. Files come and go.
- `collect_run`: append a fresh row per collect.

## TODO file matching

Globbed at the repo root and one level deep. Case-insensitive name match against:

```
pr_todo.md
TODO.md
*todo*.md
```

Within a matched file, `^\s*- \[( |x)\]` is the checkbox detector. Empty brackets count as open; `x` (any case) counts as done. Lines without a checkbox are still counted toward `total_lines`.

## Author resolution

In order:

1. `WORKLOG_AUTHOR` env var.
2. `git config --global user.name`.
3. `$USER`.

Identical to `daily_worklog.py` so worklog and suggester agree on what "your" commits are. Disable with `--all-authors`.

## Future extensions

Not implemented in MVP — recorded so we don't relitigate later.

- **Embedding-based Relevance**: replace token Jaccard with sentence embeddings of commit subjects; would need an embedding cache table.
- **PR / Issue integration**: GitHub/GitLab API for open PR counts and review-requested signal.
- **Feedback-driven weight tuning**: capture user picks vs. ranking; learn per-user weight vector.
- **Trend window**: F and I currently use one fixed window; add `--window 7d|30d` flags.
- **Notifications**: scheduled top-3 to Slack / email at start of day.
