#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0.3"]
# ///
"""Create Jira Tasks under owned epics from refined worklog markdown.

This is the inverse of jira_done.py. Because Jira refuses to backdate `created`
and `resolutiondate`, the real work period is recorded in writable date fields
(시작 날짜 / 기한) and restated in the description alongside the commit SHAs.

The default run is a dry run; --apply is required to write anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

import epic_classifier as ec
import issue_plan
import work_triage
from jira_client import JiraConfig, JiraError, request
from refine_worklog import call_claude, call_gemini, call_kimi
from issue_plan import IssuePlan, PlanError, PlannedTask
from jira_issue import (
    DEFAULT_SKIP_TAGS,
    DEFAULT_TASK_TAGS,
    EPIC_HIERARCHY_LEVEL,
    EPIC_TYPE_NAMES,
    SUBTASK_HIERARCHY_LEVEL,
    SUBTASK_TYPE_NAMES,
    SUMMARY_LIMIT,
    TASK_HIERARCHY_LEVEL,
    TASK_TYPE_NAMES,
    DateFields,
    build_description,
    build_parent_payload,
    build_payload,
    epic_prefix,
    is_trivial,
    plan_ledger_key,
    prefixed_summary,
    prune_plan,
    select_date_fields,
    select_issue_type,
    truncate_summary,
    work_period,
)
from jira_issue import _meta_values  # noqa: F401 - fetchers below rely on it
from worklog_parser import WorkItem, has_issue_reference, load_range

DEFAULT_PROJECT_KEY = "DC"
STATE_FILENAME = ".jira_created.jsonl"
# Worklogs live here when the repo root has been tidied up.
WORKLOG_DIRECTORY_CANDIDATES = ("worklogs", ".")

# Task tags that mark bookkeeping rather than a unit of work worth an issue.
DEFAULT_SKIP_TAGS = ("chore", "style", "ci")
# Sub-task is the default level, so a bucket this size or larger gets its own
# parent Task; anything smaller is promoted to a plain Task.
DEFAULT_MIN_GROUP = 2
DEFAULT_TASK_TAGS = (
    "feat", "fix", "refactor", "perf", "docs", "test",
    "chore", "build", "ci", "style", "revert",
)


MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class StateStore:
    """Append-only ledger of worklog keys already turned into Jira issues."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def created_keys(self) -> set[str]:
        """Read every worklog key recorded so far, ignoring damaged lines."""
        return set(self.created_map())

    def created_map(self) -> dict[str, str]:
        """Map each recorded key to the issue it created, ignoring damaged lines."""
        if not self.path.is_file():
            return {}

        created: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            issue_key = entry.get("issue_key")
            if isinstance(key, str):
                created[key] = issue_key if isinstance(issue_key, str) else ""
        return created

    def record(self, key: str, issue_key: str, summary: str) -> None:
        """Append one creation immediately so a crash cannot lose it."""
        entry = {"key": key, "issue_key": issue_key, "summary": summary}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stream.flush()


def build_triage_llm(work_dir: Path, timeout: int) -> work_triage.LlmCaller:
    """Build the triage caller, reusing the worklog refiner's backend chain."""

    def call(items: Sequence[WorkItem]) -> dict[str, bool]:
        prompt = work_triage.build_prompt(items)
        for backend in (call_claude, call_gemini, call_kimi):
            answer = backend(prompt, work_dir=work_dir, timeout=timeout)
            if answer:
                return work_triage.parse_response(answer)
        raise JiraError("every LLM backend failed during triage")

    return call


def resolve_worklog_dir(explicit: str | None, config: Mapping[str, Any]) -> Path:
    """Locate the worklog directory from the flag, config, or convention."""
    if explicit:
        return Path(explicit)
    configured = config.get("worklog_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    for candidate in WORKLOG_DIRECTORY_CANDIDATES:
        path = Path(candidate)
        if path.is_dir() and any(path.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-*.md")):
            return path
    return Path(".")


def fetch_account_id(config: JiraConfig) -> str:
    """Look up the caller's accountId, which assignee requires."""
    response = request(config, "GET", "/rest/api/3/myself")
    account_id = response.get("accountId") if isinstance(response, dict) else None
    if not isinstance(account_id, str) or not account_id:
        raise JiraError("Jira did not return an accountId for the current user")
    return account_id


def fetch_issue_types(config: JiraConfig, project_key: str) -> list[dict[str, Any]]:
    """Read the project's creatable issue types."""
    response = request(
        config, "GET", f"/rest/api/3/issue/createmeta/{project_key}/issuetypes"
    )
    values = _meta_values(response, "issueTypes")
    if not values:
        raise JiraError(f"no creatable issue types for project {project_key}")
    return values


def fetch_creatable_fields(
    config: JiraConfig, project_key: str, issue_type_id: str
) -> set[str]:
    """Read which fields the create screen actually accepts."""
    response = request(
        config,
        "GET",
        f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{issue_type_id}",
    )
    values = _meta_values(response, "fields")
    if not values:
        raise JiraError("Jira did not return create metadata fields")
    return {
        value["fieldId"] for value in values if isinstance(value.get("fieldId"), str)
    }


def fetch_epics(
    config: JiraConfig, project_key: str, epic_type_id: str
) -> list[ec.Epic]:
    """List the epics assigned to the current user.

    The query uses the issue type id because JQL silently returns nothing for a
    localized type name (`issuetype = "에픽"` matches zero issues; `Epic` works).
    """
    jql = (
        f"project = {project_key} AND issuetype = {epic_type_id} "
        "AND assignee = currentUser() ORDER BY created DESC"
    )
    response = request(
        config,
        "POST",
        "/rest/api/3/search/jql",
        {"jql": jql, "fields": ["summary"], "maxResults": 100},
    )
    issues = response.get("issues") if isinstance(response, dict) else None
    if not isinstance(issues, list):
        raise JiraError("Jira response does not contain an issues list")

    epics: list[ec.Epic] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        fields = issue.get("fields")
        summary = fields.get("summary") if isinstance(fields, dict) else None
        key = issue.get("key")
        if isinstance(key, str) and isinstance(summary, str):
            epics.append(ec.Epic(key=key, summary=summary))
    return epics


def create_issue(config: JiraConfig, payload: dict[str, Any]) -> str:
    """Create one issue, retrying only throttling and server faults."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = request(config, "POST", "/rest/api/3/issue", payload)
        except JiraError as exc:
            retryable = exc.status in RETRYABLE_STATUSES
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(BACKOFF_SECONDS ** attempt)
            continue

        key = response.get("key") if isinstance(response, dict) else None
        if not isinstance(key, str) or not key:
            raise JiraError("Jira did not return a key for the created issue")
        return key

    raise JiraError("issue creation exhausted all attempts")


def issue_url(base_url: str, issue_key: str) -> str:
    """Build the browse URL for an issue."""
    return f"{base_url.rstrip('/')}/browse/{issue_key}"


def write_back_issue_keys(
    updates: Sequence[tuple[WorkItem, str]], base_url: str
) -> int:
    """Append a clickable issue link to each worklog line that produced an issue.

    Lines that already name an issue are left alone, so a re-run never
    rewrites history and never double-tags a line.
    """
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for item, issue_key in updates:
        if item.source_path is None or item.source_line is None:
            continue
        by_file.setdefault(item.source_path, []).append((item.source_line, issue_key))

    written = 0
    for path, edits in by_file.items():
        if not path.is_file():
            continue

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        changed = False
        for line_number, issue_key in edits:
            index = line_number - 1
            if not 0 <= index < len(lines):
                continue
            line = lines[index]
            if has_issue_reference(line):
                continue
            link = f"[{issue_key}]({issue_url(base_url, issue_key)})"
            lines[index] = f"{line.rstrip()} {link}"
            changed = True
            written += 1

        if changed:
            trailing = "\n" if text.endswith("\n") else ""
            _atomic_write(path, "\n".join(lines) + trailing)
    return written


def _atomic_write(path: Path, text: str) -> None:
    """Replace a file's contents without leaving it half-written."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_config(path: Path) -> dict[str, Any]:
    """Read config.yaml as a mapping."""
    if not path.is_file():
        raise JiraError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise JiraError(f"config root must be a mapping: {path}")
    return raw


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Create Jira issues under owned epics from worklog markdown."
    )
    parser.add_argument("--start", help="Inclusive start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="Inclusive end date (YYYY-MM-DD).")
    parser.add_argument("--config", default="config.yaml", help="YAML config path.")
    parser.add_argument(
        "--worklog-dir",
        help="Directory holding YYYY-MM-DD.md worklogs. Default: config or ./worklogs",
    )
    parser.add_argument("--state", help=f"State ledger path. Default: ./{STATE_FILENAME}")
    parser.add_argument(
        "--apply", action="store_true", help="Actually create issues (default: dry run)."
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "--include-trivial",
        action="store_true",
        help="Also create issues for chore-only lines (default: skip them).",
    )
    parser.add_argument(
        "--no-triage",
        action="store_true",
        help="Skip the LLM pass that assigns levels and drops minor work.",
    )
    parser.add_argument(
        "--plan-out",
        help="Where to write the editable plan. Default: jira_plan_<start>_<end>.yaml",
    )
    parser.add_argument(
        "--from-plan",
        help="Apply an edited plan file instead of building one.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Jira issue creation command."""
    args = build_parser().parse_args(argv)
    try:
        config_path = Path(args.config)
        raw_config = load_config(config_path)
        config = JiraConfig.load(config_path)

        loaded_plan = (
            issue_plan.load(Path(args.from_plan)) if args.from_plan else None
        )
        start, end = _resolve_range(args, loaded_plan)

        project_key = (
            (loaded_plan.project_key if loaded_plan else None)
            or raw_config.get("JIRA_PROJECT_KEY")
            or DEFAULT_PROJECT_KEY
        )

        worklog_dir = resolve_worklog_dir(args.worklog_dir, raw_config)
        all_items = load_range(worklog_dir, start, end)
        if not all_items:
            sys.stdout.write(f"No worklog items between {start} and {end}\n")
            return 0
        items_by_key = {item.key(): item for item in all_items}

        account_id = fetch_account_id(config)
        issue_types = fetch_issue_types(config, project_key)
        task_type = select_issue_type(issue_types, TASK_HIERARCHY_LEVEL, TASK_TYPE_NAMES)
        epic_type = select_issue_type(issue_types, EPIC_HIERARCHY_LEVEL, EPIC_TYPE_NAMES)
        subtask_type = select_issue_type(
            issue_types, SUBTASK_HIERARCHY_LEVEL, SUBTASK_TYPE_NAMES
        )
        date_fields = select_date_fields(
            fetch_creatable_fields(config, project_key, task_type["id"])
        )
        subtask_date_fields = select_date_fields(
            fetch_creatable_fields(config, project_key, subtask_type["id"])
        )

        counts = {"tracked": 0, "trivial": 0, "minor": 0, "excluded": 0, "unresolved": 0}
        minor: list[WorkItem] = []
        unresolved: list[WorkItem] = []

        state = StateStore(Path(args.state) if args.state else Path(STATE_FILENAME))
        recorded = state.created_map()

        if loaded_plan is not None:
            plan = loaded_plan
        else:
            plan = _build_plan(
                all_items,
                raw_config=raw_config,
                config=config,
                args=args,
                project_key=project_key,
                epic_type=epic_type,
                start=start,
                end=end,
                counts=counts,
                minor=minor,
                unresolved=unresolved,
                recorded=recorded,
            )
    except (JiraError, ec.EpicMapError, PlanError, OSError, ValueError, yaml.YAMLError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    pending = prune_plan(plan, set(recorded))
    counts["tracked"] += len(plan.item_keys()) - len(pending.item_keys())

    missing = [key for key in pending.item_keys() if key not in items_by_key]
    if missing:
        sys.stderr.write(
            f"ERROR: plan refers to {len(missing)} worklog key(s) not found in "
            f"{worklog_dir} for {start}~{end}\n"
        )
        return 1

    plan_path = Path(
        args.plan_out
        or args.from_plan
        or f"jira_plan_{start:%Y%m%d}_{end:%Y%m%d}.yaml"
    )
    if not (args.from_plan and not args.plan_out):
        issue_plan.save(pending, plan_path)

    _report_plan(pending, counts, minor, unresolved, date_fields, project_key)
    sys.stdout.write(f"\nPlan file: {plan_path}\n")

    if not args.apply:
        sys.stdout.write("Edit it if needed, then re-run with --apply --from-plan.\n")
        return 0
    if not pending.tasks:
        return 0
    total = len(pending.item_keys()) + sum(
        1 for task in pending.tasks if task.key is None
    )
    if not args.yes and not _confirm(total):
        sys.stdout.write("Aborted.\n")
        return 0

    return _apply_plan(
        pending,
        config=config,
        state=state,
        recorded=recorded,
        items_by_key=items_by_key,
        project_key=project_key,
        account_id=account_id,
        task_type_id=str(task_type["id"]),
        subtask_type_id=str(subtask_type["id"]),
        date_fields=date_fields,
        subtask_date_fields=subtask_date_fields,
    )


def _resolve_range(args: argparse.Namespace, plan: IssuePlan | None) -> tuple[date, date]:
    """Take the date range from the flags, or from the plan being applied."""
    if args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    elif plan is not None:
        start, end = plan.start, plan.end
    else:
        raise ValueError("--start and --end are required unless --from-plan is given")
    if start > end:
        raise ValueError("start date must be on or before end date")
    return start, end


def _build_plan(
    all_items: Sequence[WorkItem],
    *,
    raw_config: dict[str, Any],
    config: JiraConfig,
    args: argparse.Namespace,
    project_key: str,
    epic_type: dict[str, Any],
    start: date,
    end: date,
    counts: dict[str, int],
    minor: list[WorkItem],
    unresolved: list[WorkItem],
    recorded: Mapping[str, str],
) -> IssuePlan:
    """Turn worklog items into the three-level structure to be created."""
    items = [item for item in all_items if item.issue_key is None]
    counts["tracked"] += len(all_items) - len(items)

    skip_tags = frozenset(
        () if args.include_trivial else (raw_config.get("skip_tags") or DEFAULT_SKIP_TAGS)
    )
    task_tags = frozenset(raw_config.get("task_tags") or DEFAULT_TASK_TAGS)
    substantial = [item for item in items if not is_trivial(item, skip_tags, task_tags)]
    counts["trivial"] = len(items) - len(substantial)

    if args.no_triage:
        standalone_items: list[WorkItem] = []
        grouped_items = substantial
    else:
        work_dir = Path(os.environ.get("WORK_DIR", "/tmp/worklog-cwd"))
        work_dir.mkdir(parents=True, exist_ok=True)
        triaged = work_triage.triage(
            substantial,
            llm=build_triage_llm(work_dir, int(os.environ.get("TIMEOUT_SEC", "180"))),
        )
        standalone_items = triaged.tasks
        grouped_items = triaged.subtasks
        minor.extend(triaged.minor)
    counts["minor"] = len(minor)

    raw_epic_map = raw_config.get("epic_map") or {}
    if not isinstance(raw_epic_map, dict):
        raise JiraError("epic_map must be a mapping of group/project to epic")

    project_groups = ec.project_groups_from_config(raw_config)
    ec.validate_epic_map_keys(
        raw_epic_map,
        project_groups,
        declared_groups=ec.declared_groups_from_config(raw_config),
    )

    epics = fetch_epics(config, project_key, str(epic_type["id"]))
    if not epics:
        raise JiraError("no epics assigned to the current user")
    epic_map = ec.resolve_epic_map(raw_epic_map, epics)

    def classify(batch: Sequence[WorkItem]) -> list[ec.Assignment]:
        outcome = ec.classify(
            batch,
            project_groups=project_groups,
            epic_map=epic_map,
            epics=epics,
            llm=None,
        )
        counts["excluded"] += len(outcome.excluded)
        counts["unresolved"] += len(outcome.unresolved)
        unresolved.extend(outcome.unresolved)
        return outcome.assigned

    standalone = classify(standalone_items)
    grouped = classify(grouped_items)

    # A bucket whose parent Task already exists keeps using it, however few
    # items are left; otherwise a resumed run would strand them beside it.
    prefixes = {epic.key: issue_plan.epic_prefix(epic.summary) for epic in epics}
    forced = set()
    for entry in grouped:
        project, epic_key = entry.item.project, entry.epic_key
        candidate = PlannedTask(
            summary=issue_plan.prefixed(f"{project} 작업", prefixes.get(epic_key)),
            epic_key=epic_key,
        )
        if plan_ledger_key(candidate, start, end) in recorded:
            forced.add((project, epic_key))

    return issue_plan.build_plan(
        standalone=standalone,
        grouped=grouped,
        epics=epics,
        project_key=project_key,
        start=start,
        end=end,
        min_group=int(raw_config.get("min_group", DEFAULT_MIN_GROUP)),
        forced=forced,
    )


def _apply_plan(
    plan: IssuePlan,
    *,
    config: JiraConfig,
    state: StateStore,
    recorded: Mapping[str, str],
    items_by_key: Mapping[str, WorkItem],
    project_key: str,
    account_id: str,
    task_type_id: str,
    subtask_type_id: str,
    date_fields: DateFields,
    subtask_date_fields: DateFields,
) -> int:
    """Create everything the plan describes, recording progress as it goes."""
    created = 0
    written_back: list[tuple[WorkItem, str]] = []

    def fail(label: str, exc: JiraError) -> int:
        sys.stderr.write(f"ERROR: {label}: {exc}\n")
        write_back_issue_keys(written_back, config.base_url)
        sys.stderr.write(
            f"Created {created} issue(s) before failing; re-run to resume\n"
        )
        return 1

    for task in plan.tasks:
        if task.key is not None and not task.subtasks:
            item = items_by_key[task.key]
            try:
                issue_key = create_issue(
                    config,
                    build_payload(
                        ec.Assignment(item=item, epic_key=task.epic_key),
                        project_key=project_key,
                        issue_type_id=task_type_id,
                        account_id=account_id,
                        date_fields=date_fields,
                        summary=task.summary,
                    ),
                )
            except JiraError as exc:
                return fail(task.summary, exc)
            state.record(task.key, issue_key, task.summary)
            written_back.append((item, issue_key))
            created += 1
            sys.stdout.write(f"{issue_key}  {task.summary}\n")
            continue

        ledger_key = task.key or plan_ledger_key(task, plan.start, plan.end)
        parent_key = recorded.get(ledger_key)
        children = [items_by_key[s.key] for s in task.subtasks]
        if not parent_key:
            try:
                parent_key = create_issue(
                    config,
                    build_parent_payload(
                        task,
                        children,
                        project_key=project_key,
                        issue_type_id=task_type_id,
                        account_id=account_id,
                        date_fields=date_fields,
                    ),
                )
            except JiraError as exc:
                return fail(task.summary, exc)
            state.record(ledger_key, parent_key, task.summary)
            created += 1
            sys.stdout.write(f"{parent_key}  {task.summary}\n")

        for planned in task.subtasks:
            item = items_by_key[planned.key]
            try:
                issue_key = create_issue(
                    config,
                    build_payload(
                        ec.Assignment(item=item, epic_key=task.epic_key),
                        project_key=project_key,
                        issue_type_id=subtask_type_id,
                        account_id=account_id,
                        date_fields=subtask_date_fields,
                        parent_key=parent_key,
                        summary=planned.summary,
                    ),
                )
            except JiraError as exc:
                return fail(planned.summary, exc)
            state.record(planned.key, issue_key, planned.summary)
            written_back.append((item, issue_key))
            created += 1
            sys.stdout.write(f"  {issue_key}  {planned.summary}\n")

    updated = write_back_issue_keys(written_back, config.base_url)
    sys.stdout.write(f"\nCreated {created} issue(s); wrote {updated} back to worklogs\n")
    return 0


def _report_plan(
    plan: IssuePlan,
    counts: Mapping[str, int],
    minor: Sequence[WorkItem],
    unresolved: Sequence[WorkItem],
    date_fields: DateFields,
    project_key: str,
) -> None:
    """Print the tree that will be created before anything is written."""
    parents = sum(1 for task in plan.tasks if task.key is None)
    leaves = len(plan.item_keys())
    sys.stdout.write(
        f"project={project_key} start-field={date_fields.start or '-'} "
        f"end-field={date_fields.end or '-'}\n"
    )
    sys.stdout.write(
        f"tasks={len(plan.tasks)} items={leaves} groups={parents} "
        f"tracked={counts.get('tracked', 0)} trivial={counts.get('trivial', 0)} "
        f"minor={counts.get('minor', 0)} excluded={counts.get('excluded', 0)} "
        f"unresolved={counts.get('unresolved', 0)}\n\n"
    )
    for task in plan.tasks:
        sys.stdout.write(f"  {task.epic_key} <- {task.summary}\n")
        for planned in task.subtasks:
            sys.stdout.write(f"      하위작업: {planned.summary}\n")
    if minor:
        sys.stdout.write("\nToo minor to track (not created):\n")
        for item in minor:
            sys.stdout.write(f"  [{item.project}] {item.summary}\n")
    if unresolved:
        sys.stdout.write("\nUnresolved (no epic; not created):\n")
        for item in unresolved:
            sys.stdout.write(f"  [{item.project}] {item.summary}\n")


def _confirm(count: int) -> bool:
    """Ask before writing to Jira."""
    answer = input(f"Create {count} Jira issue(s)? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


if __name__ == "__main__":
    raise SystemExit(main())
