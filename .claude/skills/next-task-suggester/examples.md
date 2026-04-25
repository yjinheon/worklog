# next-task-suggester — examples

Concrete input/output pairs for the recommender. Use these to spot-check format and tag assignment after editing `score.py` or `suggest.py`.

## Example 1: typical morning, mixed activity

### Input (state)

| repo                    | last commit | last-5 span | open TODO | runs w/ commits (last 5) |
|-------------------------|-------------|-------------|-----------|--------------------------|
| worklog                 | 2h ago      | ~6h avg     | 0         | 5/5                      |
| data-engineering-skils  | 1d ago      | ~12h avg    | 3         | 4/5                      |
| dataflow                | 6d ago      | sparse      | 7         | 0/5                      |
| dbtflow                 | 14d ago     | sparse      | 0         | 0/5                      |
| spark-processing        | 32d ago     | n/a         | 1         | 0/5                      |

### Output (default mode)

```
# Today's suggestions (2026-04-25)

1. worklog  [continue]
   - Recent: 2h ago, 5 commits in last-5 (avg ~6h)
   - TODO: none
   - Score 0.78 (R 0.97 · F 1.00 · T 0.00 · I 1.00 · Rel 0.05)

2. data-engineering-skils  [continue]
   - Recent: 1d ago, 5 commits in last-5 (avg ~12h)
   - TODO: pr_todo.md (3 open)
     · "[ ] dbt incremental example for slowly changing dim"
     · "[ ] add ruff to skills CI"
     · "[ ] doc: snowflake query optimization patterns"
   - Score 0.71 (R 0.71 · F 1.00 · T 0.60 · I 0.80 · Rel 0.18)

3. dataflow  [resume]
   - Last commit 6d ago; 7 open TODOs
   - TODO: pr_todo.md (7 open)
     · "[ ] backfill spark job for Q1 metrics"
     · "[ ] split bronze/silver layers"
     · "[ ] add dlq handler"
   - Score 0.52 (R 0.13 · F 0.20 · T 1.00 · I 0.00 · Rel 0.25)

4. dbtflow  [explore]
   - Idle 14d but tokens overlap with active 'data-engineering-skils' (dbt, model, test)
   - Score 0.21 (R 0.03 · F 0.00 · T 0.00 · I 0.00 · Rel 0.42)

5. spark-processing
   - Idle 32d, no signals trigger a tag
   - Score 0.06 (R 0.00 · F 0.00 · T 0.20 · I 0.00 · Rel 0.10)
```

### Output (`--json`, top 2)

```json
[
  {
    "rank": 1,
    "repo": "worklog",
    "project": "skills",
    "tag": "continue",
    "score": 0.78,
    "signals": {"R": 0.97, "F": 1.0, "T": 0.0, "I": 1.0, "Rel": 0.05},
    "reasons": [
      "last commit 2h ago",
      "5 commits in last-5 (avg ~6h)"
    ]
  },
  {
    "rank": 2,
    "repo": "data-engineering-skils",
    "project": "skills",
    "tag": "continue",
    "score": 0.71,
    "signals": {"R": 0.71, "F": 1.0, "T": 0.6, "I": 0.8, "Rel": 0.18},
    "reasons": [
      "last commit 1d ago",
      "pr_todo.md has 3 open items"
    ]
  }
]
```

## Example 2: post-vacation Monday (`--mode balance`)

Recency is universally cold, so balance mode pushes TODO-heavy projects up.

```
# Today's suggestions (2026-04-25)

1. dataflow  [resume]
   - Idle 9d, 7 open TODOs — likely the highest-leverage restart
   - Score 0.46 (R 0.06 · F 0.00 · T 1.00 · I 0.00 · Rel 0.25)

2. data-engineering-skils  [resume]
   - Idle 8d, 3 open TODOs
   - Score 0.33 (R 0.07 · F 0.20 · T 0.60 · I 0.40 · Rel 0.18)

3. worklog  [resume]
   - Idle 8d, no TODOs but historically the most active repo
   - Score 0.15 (R 0.07 · F 0.20 · T 0.00 · I 1.00 · Rel 0.05)
```

## Performance notes

Measured on the author's laptop with 30 repos and a warm filesystem cache:

| step                              | time  |
|-----------------------------------|-------|
| `collect.py` (cold cache)         | ~1.4s |
| `collect.py` (warm)               | ~1.1s |
| `score.py` (DB only, 30 repos)    | <50ms |
| `suggest.py` (no refresh needed)  | <80ms |

Well under the spec's 2s budget. If the workspace grows past ~80 repos, parallelize the per-repo subprocess calls in `collect.py` with `concurrent.futures.ThreadPoolExecutor(max_workers=8)`.
