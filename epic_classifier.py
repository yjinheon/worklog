#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Assign worklog items to Jira epics.

Classification is deterministic first: a work item's project maps to a repo
group in config.yaml, and the group maps to an epic. `epic_map` may also key
a single project directly, which overrides its group. Only items that no rule
covers are handed to an LLM, and an LLM that cannot decide leaves the item
unresolved rather than guessing an epic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from worklog_parser import WorkItem

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
BRACKET_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")

LlmCaller = Callable[[Sequence[WorkItem], Sequence["Epic"]], Mapping[str, str | None]]

PROMPT_HEADER = """다음 작업 목록을 각각 가장 알맞은 Jira Epic에 배정해라.

[규칙]
- 반드시 아래 Epic 목록의 key 중 하나만 사용한다
- 확신이 없으면 epic_key를 null로 둔다. 추측해서 배정하지 말 것
- 출력은 JSON 배열 하나만. 설명, 코드블록, 사족 금지

[출력 형식]
[{"key": "<작업 key>", "epic_key": "DC-123" 또는 null}]
"""


class EpicMapError(Exception):
    """A config-level epic mapping that cannot be resolved safely."""


@dataclass(frozen=True)
class Epic:
    """A Jira epic the user owns."""

    key: str
    summary: str


@dataclass(frozen=True)
class Assignment:
    """A work item paired with the epic it belongs under."""

    item: WorkItem
    epic_key: str


@dataclass
class Classification:
    """Outcome of classifying a batch of work items."""

    assigned: list[Assignment] = field(default_factory=list)
    excluded: list[WorkItem] = field(default_factory=list)
    unresolved: list[WorkItem] = field(default_factory=list)


def project_groups_from_config(config: Mapping[str, object]) -> dict[str, str]:
    """Map repo folder name to its group, matching the worklog project naming."""
    groups: dict[str, str] = {}
    for entry in config.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        group = entry.get("group")
        if not isinstance(path, str) or not isinstance(group, str):
            continue
        name = Path(path).name
        if name:
            groups[name] = group
    return groups


def declared_groups_from_config(config: Mapping[str, object]) -> set[str]:
    """Read group names declared in config, including ones no repo uses yet."""
    declared = config.get("project_groups")
    return set(declared) if isinstance(declared, Mapping) else set()


def validate_epic_map_keys(
    raw_map: Mapping[str, str | None],
    project_groups: Mapping[str, str],
    *,
    declared_groups: Iterable[str] | None = None,
) -> None:
    """Reject epic_map keys that name nothing, or that name two things.

    Catching typos here keeps a misspelled project from silently falling back
    to its group mapping. A group declared in config but not yet used by any
    repo is valid, so callers pass declared_groups to widen the known set.
    """
    known_groups = set(project_groups.values()) | set(declared_groups or ())
    known_projects = set(project_groups)

    for key in raw_map:
        is_group = key in known_groups
        is_project = key in known_projects
        if is_group and is_project:
            raise EpicMapError(
                f"epic_map[{key}] is ambiguous: it names both a group and a project"
            )
        if not is_group and not is_project:
            raise EpicMapError(
                f"epic_map[{key}] matches no repo group or project in config"
            )


def resolve_epic_map(
    raw_map: Mapping[str, str | None], epics: Sequence[Epic]
) -> dict[str, str | None]:
    """Turn configured epic keys or names into concrete epic keys.

    A None value marks a group as excluded. Anything that resolves to zero or
    to multiple epics raises instead of picking one arbitrarily.
    """
    by_key = {epic.key: epic for epic in epics}
    resolved: dict[str, str | None] = {}

    for group, value in raw_map.items():
        if value is None:
            resolved[group] = None
            continue

        if not isinstance(value, str) or not value.strip():
            raise EpicMapError(f"epic_map[{group}] must be an epic key, name, or null")

        candidate = value.strip()
        if ISSUE_KEY_PATTERN.match(candidate):
            if candidate not in by_key:
                raise EpicMapError(
                    f"epic_map[{group}]: {candidate} is not an epic you own"
                )
            resolved[group] = candidate
            continue

        matches = [
            epic for epic in epics if _normalize(candidate) in _normalize(epic.summary)
        ]
        if not matches:
            raise EpicMapError(f"epic_map[{group}]: no epic matches {candidate!r}")
        if len(matches) > 1:
            keys = ", ".join(epic.key for epic in matches)
            raise EpicMapError(
                f"epic_map[{group}]: {candidate!r} matches multiple epics ({keys})"
            )
        resolved[group] = matches[0].key

    return resolved


def classify(
    items: Iterable[WorkItem],
    *,
    project_groups: Mapping[str, str],
    epic_map: Mapping[str, str | None],
    epics: Sequence[Epic],
    llm: LlmCaller | None = None,
) -> Classification:
    """Assign items to epics, deferring only unmapped ones to the LLM."""
    result = Classification()
    deferred: list[WorkItem] = []

    for item in items:
        # A project entry is more specific than the group entry, so it wins.
        if item.project in epic_map:
            lookup: str | None = item.project
        else:
            group = project_groups.get(item.project)
            lookup = group if group is not None and group in epic_map else None

        if lookup is None:
            deferred.append(item)
            continue

        epic_key = epic_map[lookup]
        if epic_key is None:
            result.excluded.append(item)
            continue
        result.assigned.append(Assignment(item=item, epic_key=epic_key))

    if not deferred:
        return result

    if llm is None:
        result.unresolved.extend(deferred)
        return result

    try:
        answers = llm(deferred, epics)
    except Exception:  # noqa: BLE001 - a failed LLM must not drop mapped work
        result.unresolved.extend(deferred)
        return result

    known_keys = {epic.key for epic in epics}
    for item in deferred:
        epic_key = answers.get(item.key()) if answers else None
        if isinstance(epic_key, str) and epic_key in known_keys:
            result.assigned.append(Assignment(item=item, epic_key=epic_key))
        else:
            result.unresolved.append(item)

    return result


def build_prompt(items: Sequence[WorkItem], epics: Sequence[Epic]) -> str:
    """Build the epic-selection prompt for the unmapped items."""
    lines = [PROMPT_HEADER, "[Epic 목록]"]
    lines.extend(f"- {epic.key}: {epic.summary}" for epic in epics)
    lines.append("")
    lines.append("[작업 목록]")
    for item in items:
        tags = " ".join(f"#{tag}" for tag in item.tags)
        lines.append(f"- key={item.key()} project={item.project} {tags}".rstrip())
        lines.append(f"  {item.summary}")
    return "\n".join(lines)


def parse_llm_response(text: str) -> dict[str, str]:
    """Extract the JSON array from noisy CLI output, ignoring abstentions."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return {}

    try:
        decoded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}

    if not isinstance(decoded, list):
        return {}

    answers: dict[str, str] = {}
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        epic_key = entry.get("epic_key")
        if isinstance(key, str) and isinstance(epic_key, str) and epic_key:
            answers[key] = epic_key
    return answers


def _normalize(text: str) -> str:
    """Normalize an epic name for tolerant matching."""
    without_prefix = BRACKET_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", without_prefix).strip().casefold()
