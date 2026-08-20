#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Shaping Jira issues: which types and fields to use, and what to send.

Everything here is pure — it decides shapes from metadata already fetched, so
it can be exercised without touching the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import epic_classifier as ec
import issue_plan
from issue_plan import PlannedTask
from jira_client import JiraError
from worklog_parser import WorkItem

TASK_HIERARCHY_LEVEL = 0
EPIC_HIERARCHY_LEVEL = 1
SUBTASK_HIERARCHY_LEVEL = -1
# A project can expose several types at one hierarchy level (스토리/작업/버그),
# so the name decides and the level is only the fallback.
TASK_TYPE_NAMES = ("Task", "작업")
EPIC_TYPE_NAMES = ("Epic", "에픽")
SUBTASK_TYPE_NAMES = ("Subtask", "Sub-task", "하위 작업")

# 시작 날짜 / Target start, then 기한 / Target end. First available wins.
START_DATE_FIELDS = ("customfield_10015", "customfield_10121")
END_DATE_FIELDS = ("duedate", "customfield_10122")

# Jira rejects a summary longer than this; the full text stays in the body.
SUMMARY_LIMIT = 255

# Task tags that mark bookkeeping rather than a unit of work worth an issue.
DEFAULT_SKIP_TAGS = ("chore", "style", "ci")
DEFAULT_TASK_TAGS = (
    "feat", "fix", "refactor", "perf", "docs", "test",
    "chore", "build", "ci", "style", "revert",
)

epic_prefix = issue_plan.epic_prefix
prefixed_summary = issue_plan.prefixed


@dataclass(frozen=True)
class DateFields:
    """Field ids used to record the real work period."""

    start: str | None
    end: str | None


def is_trivial(
    item: WorkItem, skip_tags: frozenset[str], task_tags: frozenset[str]
) -> bool:
    """Report whether an item is bookkeeping rather than a unit of work.

    Only task tags decide; system tags such as #kubernetes never make a chore
    line substantial. A line carrying no task tag at all cannot be judged and
    is kept.
    """
    if not skip_tags:
        return False
    present = [tag for tag in item.tags if tag in task_tags]
    return bool(present) and all(tag in skip_tags for tag in present)


def select_issue_type(
    issue_types: Sequence[dict[str, Any]],
    hierarchy_level: int,
    preferred_names: Sequence[str],
) -> dict[str, Any]:
    """Pick an issue type by name, falling back to its hierarchy level.

    Matching the name first keeps 작업 from losing to 스토리, which sits at the
    same level; the level fallback keeps unusual project setups working.
    """
    at_level = [
        issue_type
        for issue_type in issue_types
        if issue_type.get("hierarchyLevel") == hierarchy_level
    ]
    if not at_level:
        raise JiraError(f"project has no issue type at hierarchy level {hierarchy_level}")

    wanted = {name.casefold() for name in preferred_names}
    for issue_type in at_level:
        names = {
            str(issue_type.get(field, "")).casefold()
            for field in ("untranslatedName", "name")
        }
        if names & wanted:
            return issue_type
    return at_level[0]


def _meta_values(response: Any, key: str) -> list[dict[str, Any]]:
    """Read a createmeta collection, tolerating the `values` alias."""
    if not isinstance(response, dict):
        return []
    values = response.get(key)
    if not isinstance(values, list):
        values = response.get("values")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def select_date_fields(available: set[str]) -> DateFields:
    """Choose the best available start/end date fields, or none."""
    return DateFields(
        start=next((name for name in START_DATE_FIELDS if name in available), None),
        end=next((name for name in END_DATE_FIELDS if name in available), None),
    )


def work_period(item: WorkItem) -> tuple[date, date]:
    """Resolve the real start and end dates for one work item."""
    start = item.source_date
    end = item.completion_date or item.source_date
    if end < start:
        start, end = end, start
    return start, end


def build_description(item: WorkItem) -> dict[str, Any]:
    """Build an ADF description; Jira v3 rejects a plain string here."""
    start, end = work_period(item)
    provenance = [f"worklog-key: {item.key()}", f"기간: {start} ~ {end}"]
    if item.tags:
        provenance.append("태그: " + ", ".join(f"#{tag}" for tag in item.tags))
    if item.shas:
        provenance.append("커밋: " + ", ".join(item.shas))

    return {
        "type": "doc",
        "version": 1,
        "content": [
            _paragraph(item.summary),
            _paragraph(f"출처: 워크로그 {item.source_date} / {item.project}"),
            *(_paragraph(line) for line in provenance),
        ],
    }


def build_payload(
    assignment: ec.Assignment,
    *,
    project_key: str,
    issue_type_id: str,
    account_id: str,
    date_fields: DateFields,
    parent_key: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Build the create-issue payload for one worklog item.

    `parent_key` overrides the epic when the item becomes a sub-task, and
    `summary` overrides the worklog wording when the plan renamed it.
    """
    item = assignment.item
    start, end = work_period(item)

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "issuetype": {"id": issue_type_id},
        "parent": {"key": parent_key or assignment.epic_key},
        "assignee": {"accountId": account_id},
        "summary": truncate_summary(summary or item.summary),
        "description": build_description(item),
    }
    if date_fields.start:
        fields[date_fields.start] = start.isoformat()
    if date_fields.end:
        fields[date_fields.end] = end.isoformat()
    return {"fields": fields}


def build_parent_payload(
    task: PlannedTask,
    children: Sequence[WorkItem],
    *,
    project_key: str,
    issue_type_id: str,
    account_id: str,
    date_fields: DateFields,
) -> dict[str, Any]:
    """Build the parent Task that holds a plan group's sub-tasks."""
    start = min(work_period(child)[0] for child in children)
    end = max(work_period(child)[1] for child in children)

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "issuetype": {"id": issue_type_id},
        "parent": {"key": task.epic_key},
        "assignee": {"accountId": account_id},
        "summary": truncate_summary(task.summary),
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                _paragraph(f"{start} ~ {end} 작업 묶음"),
                *(_paragraph(f"- {child.summary}") for child in children),
            ],
        },
    }
    if date_fields.start:
        fields[date_fields.start] = start.isoformat()
    if date_fields.end:
        fields[date_fields.end] = end.isoformat()
    return {"fields": fields}


def prune_plan(plan: IssuePlan, done: set[str]) -> IssuePlan:
    """Drop entries already created, keeping a parent only if work remains."""
    tasks: list[PlannedTask] = []
    for task in plan.tasks:
        subtasks = tuple(s for s in task.subtasks if s.key not in done)
        if task.key is None:
            if subtasks:
                tasks.append(replace(task, subtasks=subtasks))
            continue
        if task.key not in done:
            tasks.append(replace(task, subtasks=subtasks))
        elif subtasks:
            tasks.append(replace(task, subtasks=subtasks))
    return replace(plan, tasks=tuple(tasks))


def plan_ledger_key(task: PlannedTask, start: date, end: date) -> str:
    """Ledger key for a parent Task, which no worklog line backs.

    Keyed on the run range and the parent's name — both stable across a resume,
    so remaining sub-tasks rejoin the parent instead of spawning a new one.
    """
    return f"group|{start}|{end}|{task.epic_key}|{task.summary}"


def truncate_summary(summary: str) -> str:
    """Fit a summary inside Jira's limit without losing the full text."""
    if len(summary) <= SUMMARY_LIMIT:
        return summary
    return summary[: SUMMARY_LIMIT - 1].rstrip() + "…"


def _paragraph(text: str) -> dict[str, Any]:
    """Wrap one line of text as an ADF paragraph."""
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


if __name__ == "__main__":
    raise SystemExit(main())
