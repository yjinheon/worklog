# worklog-refiner — reference

Detailed specification of the prompt assembled by `scripts/build_prompt.py`.

## Format rules (enforced via prompt)

- First line: `# Worklog YYYY-MM-DD`

- `## Summary`
  - 2–3 line prose summary (Korean)
  - Terminal-noun style, bullet points, concise
- `## Projects`
  - Use **only** categories defined in the vocab's `projects` map, as `###` headings
  - Each task: `- [x]` single-line description + inline tags (`#tag`)
  - `#todo` is required on every task
  - Additional inline tags are restricted to the **Types** whitelist below
  - Categories with no activity: `_(진행 없음)_`
- `## Repositories`
  - Each repo as a `####` heading
  - List of `<short hash> <subject>`
- `## Notes`
  - Meetings, field work, non-commit notes
  - Omit the section entirely when empty

### Task item format

```markdown
In progress
- [ ] task description #project/<project-name> #todo [due:: 2026-04-20]

Completed
- [x] task description #project/<project-name> #todo [due:: 2026-02-05] [completion:: 2026-02-09]
```

## Types (inline tag whitelist)

Besides `#todo`, only these tags are allowed:

```
feat, fix, refactor, perf, docs, test, chore, build, ci, style, revert
```

## Classification rules (repo → project)

1. Look up the current repo in `repo_mapping`.
2. If `overrides` is present, match commit subject prefixes **first**.
3. On miss, use the `default` project.
4. If the repo is absent from `repo_mapping`, classify as `unclassified`.

## Output constraints

- Pure Markdown only
- Do NOT wrap the whole document in a code fence
- No commentary, preamble, or trailing notes
- Korean language

## Prompt section layout

`build_prompt.py` concatenates in this order:

1. Header (instructions + format rules + classification rules + output constraints + examples)
2. `[프로젝트 매핑 사전]` — the raw contents of `vocab.yaml`, if supplied
3. `[입력 데이터]` — the entire raw markdown input
