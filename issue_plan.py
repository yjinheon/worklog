#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0.3"]
# ///
"""The reviewable plan that sits between worklog parsing and Jira creation.

A dry run writes this plan out as YAML so the structure can be corrected by
hand — moving a sub-task under a different Task, renaming an issue, changing
an epic — before anything is created. `jira_create.py --from-plan` then applies
exactly what the file says.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

import epic_classifier as ec

PLAN_VERSION = 1
EPIC_PREFIX = re.compile(r"^\s*\[(?P<label>[^\]]+)\]\s*")


class PlanError(Exception):
    """A plan file that cannot be applied as written."""


@dataclass(frozen=True)
class PlannedSubtask:
    """One worklog item that will become a sub-task."""

    key: str
    summary: str


@dataclass(frozen=True)
class PlannedTask:
    """A Task: either backed by one worklog item, or a parent holding sub-tasks."""

    summary: str
    epic_key: str
    key: str | None = None
    subtasks: tuple[PlannedSubtask, ...] = ()


@dataclass(frozen=True)
class IssuePlan:
    """Everything one run intends to create."""

    project_key: str
    start: date
    end: date
    tasks: tuple[PlannedTask, ...] = ()

    def item_keys(self) -> list[str]:
        """Every worklog key the plan refers to."""
        keys: list[str] = []
        for task in self.tasks:
            if task.key:
                keys.append(task.key)
            keys.extend(subtask.key for subtask in task.subtasks)
        return keys


def epic_prefix(epic_summary: str) -> str | None:
    """Read the [Data]/[AI] label an epic names itself with."""
    match = EPIC_PREFIX.match(epic_summary)
    return match.group("label").strip() if match else None


def prefixed(summary: str, prefix: str | None) -> str:
    """Put the epic's label in front of an issue name, replacing a stale one."""
    if not prefix:
        return summary
    return f"[{prefix}] {EPIC_PREFIX.sub('', summary).strip()}"


def build_plan(
    *,
    standalone: Sequence[ec.Assignment],
    grouped: Sequence[ec.Assignment],
    epics: Sequence[ec.Epic],
    project_key: str,
    start: date,
    end: date,
    min_group: int = 2,
    forced: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
) -> IssuePlan:
    """Lay out the three-level structure the run will create.

    `grouped` items become sub-tasks under one Task per project and epic;
    a bucket too small to be worth a parent is promoted to a plain Task,
    unless `forced` says a parent for it already exists.
    """
    prefixes = {epic.key: epic_prefix(epic.summary) for epic in epics}
    tasks: list[PlannedTask] = []

    for entry in standalone:
        tasks.append(_leaf_task(entry, prefixes.get(entry.epic_key)))

    buckets: dict[tuple[str, str], list[ec.Assignment]] = {}
    for entry in grouped:
        buckets.setdefault((entry.item.project, entry.epic_key), []).append(entry)

    for (project, epic_key), bucket in buckets.items():
        prefix = prefixes.get(epic_key)
        if len(bucket) < max(min_group, 1) and (project, epic_key) not in forced:
            tasks.extend(_leaf_task(entry, prefix) for entry in bucket)
            continue

        tasks.append(
            PlannedTask(
                summary=prefixed(f"{project} 작업", prefix),
                epic_key=epic_key,
                key=None,
                subtasks=tuple(
                    PlannedSubtask(
                        key=entry.item.key(),
                        summary=prefixed(entry.item.summary, prefix),
                    )
                    for entry in bucket
                ),
            )
        )

    return IssuePlan(
        project_key=project_key, start=start, end=end, tasks=tuple(tasks)
    )


def _leaf_task(entry: ec.Assignment, prefix: str | None) -> PlannedTask:
    """Build a Task backed directly by one worklog item."""
    return PlannedTask(
        summary=prefixed(entry.item.summary, prefix),
        epic_key=entry.epic_key,
        key=entry.item.key(),
    )


def save(plan: IssuePlan, path: Path) -> None:
    """Write the plan as YAML meant to be read and edited by a person."""
    document: dict[str, Any] = {
        "version": PLAN_VERSION,
        "project": plan.project_key,
        "start": plan.start.isoformat(),
        "end": plan.end.isoformat(),
        "tasks": [_task_to_dict(task) for task in plan.tasks],
    }
    header = (
        "# 이 파일을 고친 뒤 jira_create.py --from-plan 으로 적용한다.\n"
        "# - subtasks 항목을 다른 task 아래로 옮기면 부모가 바뀐다\n"
        "# - summary는 자유롭게 고쳐도 되고, 항목을 지우면 생성되지 않는다\n"
        "# - key는 워크로그 줄과 이어주는 식별자이므로 바꾸지 말 것\n"
    )
    path.write_text(
        header + yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load(path: Path) -> IssuePlan:
    """Read a plan file, rejecting anything that cannot be applied."""
    if not path.is_file():
        raise PlanError(f"plan file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PlanError(f"cannot parse plan: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise PlanError(f"plan root must be a mapping: {path}")

    tasks: list[PlannedTask] = []
    seen: set[str] = set()
    for entry in raw.get("tasks") or []:
        if not isinstance(entry, Mapping):
            raise PlanError("each task must be a mapping")
        tasks.append(_task_from_dict(entry, seen))

    return IssuePlan(
        project_key=str(raw.get("project") or ""),
        start=_as_date(raw.get("start"), "start"),
        end=_as_date(raw.get("end"), "end"),
        tasks=tuple(tasks),
    )


def _task_to_dict(task: PlannedTask) -> dict[str, Any]:
    """Serialise one Task, omitting the half that does not apply."""
    document: dict[str, Any] = {"summary": task.summary, "epic": task.epic_key}
    if task.key:
        document["key"] = task.key
    if task.subtasks:
        document["subtasks"] = [
            {"key": subtask.key, "summary": subtask.summary}
            for subtask in task.subtasks
        ]
    return document


def _task_from_dict(entry: Mapping[str, Any], seen: set[str]) -> PlannedTask:
    """Read one Task, validating what the applier depends on."""
    summary = entry.get("summary")
    epic_key = entry.get("epic")
    if not isinstance(summary, str) or not summary.strip():
        raise PlanError("each task needs a summary")
    if not isinstance(epic_key, str) or not epic_key.strip():
        raise PlanError(f"task {summary!r} needs an epic")

    subtasks: list[PlannedSubtask] = []
    for raw_subtask in entry.get("subtasks") or []:
        if not isinstance(raw_subtask, Mapping):
            raise PlanError(f"task {summary!r} has a malformed subtask")
        subtasks.append(
            PlannedSubtask(
                key=_claim_key(raw_subtask.get("key"), seen, summary),
                summary=_require_summary(raw_subtask.get("summary"), summary),
            )
        )

    key = entry.get("key")
    if key is not None:
        key = _claim_key(key, seen, summary)
    elif not subtasks:
        raise PlanError(
            f"task {summary!r} has neither a key nor subtasks, so nothing backs it"
        )

    return PlannedTask(
        summary=summary.strip(),
        epic_key=epic_key.strip(),
        key=key,
        subtasks=tuple(subtasks),
    )


def _claim_key(value: Any, seen: set[str], context: str) -> str:
    """Take a worklog key, refusing one that already appeared.

    An all-digit key round-trips through YAML as an int, so accept that too
    rather than rejecting a plan the tool itself wrote.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"task {context!r} has an entry without a key")
    key = value.strip()
    if key in seen:
        raise PlanError(f"key {key} appears more than once in the plan")
    seen.add(key)
    return key


def _require_summary(value: Any, context: str) -> str:
    """Take a subtask summary."""
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"task {context!r} has a subtask without a summary")
    return value.strip()


def _as_date(value: Any, name: str) -> date:
    """Read a plan date, accepting what PyYAML already parsed."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PlanError(f"plan {name} is not a date: {value}") from exc
    raise PlanError(f"plan is missing {name}")
