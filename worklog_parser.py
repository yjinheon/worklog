#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Parse refined worklog markdown into per-task work items.

Two refined layouts exist in this repository and both are supported:

- "projects" (refine_worklog.py output, 2026-04 ~ 2026-06)
      #### <project>
      - [x] 요약 #project/<project> #todo #feat  [due:: ...]  [completion:: ...]

- "sections" (hh_worklog.py output, 2026-07 ~)
      작업완료 사항

      - [<project>] [feat, tag] 요약 #project/<project> #feat

Raw git-log dumps (daily_worklog.py output) carry no task breakdown and are
skipped rather than guessed at.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

KEY_LENGTH = 16
FILENAME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})$")

# Headings appear with and without a trailing colon across worklog vintages.
PROJECTS_HEADING = re.compile(r"^###\s+Projects\s*:?\s*$", re.MULTILINE)
REPOSITORIES_HEADING = re.compile(r"^###\s+Repositories\s*:?\s*$", re.MULTILINE)
SECTION_HEADING = re.compile(r"^###\s+", re.MULTILINE)
RAW_HEADING = re.compile(r"^#\s+Worklog raw\b", re.MULTILINE)
# "작업사항" (2026-07-07 ~ 07-10) and "작업완료 사항" name the same section.
COMPLETED_HEADING = re.compile(r"^작업(완료\s*)?사항\s*:?\s*$", re.MULTILINE)
NEXT_TOP_SECTION = re.compile(
    r"^(이슈사항|검토사항|작업(완료\s*)?사항)\s*:?\s*$", re.MULTILINE
)

SUBHEADING = re.compile(r"^####\s+(?P<name>.+?)\s*$")
CHECKBOX = re.compile(r"^\s*-\s*\[(?P<state>[ xX])\]\s*(?P<body>.+?)\s*$")
BULLET = re.compile(r"^\s*-\s+(?P<body>.+?)\s*$")
BRACKET_PREFIX = re.compile(r"^\s*\[(?P<value>[^\]]*)\]\s*")
PROJECT_TAG = re.compile(r"#project/(?P<name>[^\s#]+)")
INLINE_TAG = re.compile(r"#(?P<name>[A-Za-z][\w-]*)")
DATE_FIELD = re.compile(r"\[(?P<name>due|completion)::\s*(?P<value>\d{4}-\d{2}-\d{2})\s*\]")
# Links a task line to the Jira issue it belongs to; jira_create.py writes it back.
# The markdown link is the canonical written-back form; the plain field is
# accepted so hand-written entries keep working.
ISSUE_LINK = re.compile(
    r"\[(?P<key>[A-Z][A-Z0-9]*-\d+)\]\((?P<url>[^)\s]*/browse/(?P=key))\)"
)
ISSUE_FIELD = re.compile(r"\[issue::\s*(?P<key>[A-Z][A-Z0-9]*-\d+)\s*\]")
SHA_LINE = re.compile(r"^\s*-?\s*(?P<sha>[0-9a-f]{7,40})\s+\S")

# Tags that describe worklog bookkeeping rather than the work itself.
IGNORED_TAGS = frozenset({"todo", "project"})
# Projects that never correspond to a repository or a Jira issue.
NON_REPO_PROJECTS = frozenset({"회의"})
PLACEHOLDER_MARKERS = ("작성 필요", "진행 없음")


@dataclass(frozen=True)
class WorkItem:
    """One task extracted from a refined worklog."""

    summary: str
    project: str
    source_date: date
    tags: tuple[str, ...] = ()
    done: bool = True
    due_date: date | None = None
    completion_date: date | None = None
    shas: tuple[str, ...] = field(default=())
    issue_key: str | None = None
    source_path: Path | None = None
    source_line: int | None = None

    def key(self) -> str:
        """Stable identity used to avoid creating the same issue twice."""
        seed = f"{self.source_date.isoformat()}|{self.project}|{self.summary}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def detect_format(text: str) -> str:
    """Classify a worklog document so unsupported layouts are skipped."""
    if RAW_HEADING.search(text):
        return "raw"
    if PROJECTS_HEADING.search(text):
        return "projects"
    if COMPLETED_HEADING.search(text):
        return "sections"
    return "unknown"


def parse_worklog(
    text: str, source_date: date, source_path: Path | None = None
) -> list[WorkItem]:
    """Extract work items from either refined layout."""
    layout = detect_format(text)
    if layout == "projects":
        return _parse_projects_layout(text, source_date, source_path)
    if layout == "sections":
        return _parse_sections_layout(text, source_date, source_path)
    return []


def parse_worklog_file(path: Path) -> list[WorkItem]:
    """Parse a `YYYY-MM-DD.md` worklog file."""
    source_date = _date_from_filename(path)
    if source_date is None:
        raise ValueError(f"worklog filename must be YYYY-MM-DD.md: {path.name}")
    return parse_worklog(path.read_text(encoding="utf-8"), source_date, path)


def load_range(directory: Path, start: date, end: date) -> list[WorkItem]:
    """Parse every worklog file whose filename date falls inside the range."""
    items: list[WorkItem] = []
    for path in sorted(directory.glob("*.md")):
        source_date = _date_from_filename(path)
        if source_date is None or not start <= source_date <= end:
            continue
        items.extend(
            parse_worklog(path.read_text(encoding="utf-8"), source_date, path)
        )
    return items


def parse_commit_shas(text: str) -> dict[str, tuple[str, ...]]:
    """Collect commit short SHAs per project from a `### Repositories` section."""
    body = _slice_projects_section(text, REPOSITORIES_HEADING)
    if body is None:
        return {}

    shas: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        heading = SUBHEADING.match(line)
        if heading:
            current = heading.group("name")
            shas.setdefault(current, [])
            continue
        if current is None:
            continue
        match = SHA_LINE.match(line)
        if match:
            shas[current].append(match.group("sha"))
    return {name: tuple(found) for name, found in shas.items() if found}


def _parse_projects_layout(
    text: str, source_date: date, source_path: Path | None = None
) -> list[WorkItem]:
    """Parse the `### Projects` checkbox layout."""
    body = _slice_projects_section(text, PROJECTS_HEADING)
    if body is None:
        return []

    shas_by_project = parse_commit_shas(text)
    line_offset = _line_offset(text, body)
    items: list[WorkItem] = []
    heading_project: str | None = None

    for index, line in enumerate(body.splitlines()):
        heading = SUBHEADING.match(line)
        if heading:
            heading_project = heading.group("name")
            continue

        match = CHECKBOX.match(line)
        if not match:
            continue

        raw_body = match.group("body")
        if _is_placeholder(raw_body):
            continue

        project = _project_of(raw_body, heading_project)
        if project is None:
            continue

        dates = {
            found.group("name"): date.fromisoformat(found.group("value"))
            for found in DATE_FIELD.finditer(raw_body)
        }
        items.append(
            WorkItem(
                summary=_clean_summary(raw_body),
                project=project,
                source_date=source_date,
                tags=_tags_of(raw_body),
                done=match.group("state").lower() == "x",
                due_date=dates.get("due"),
                completion_date=dates.get("completion"),
                shas=shas_by_project.get(project, ()),
                issue_key=_issue_key_of(raw_body),
                source_path=source_path,
                source_line=line_offset + index,
            )
        )
    return items


def _parse_sections_layout(
    text: str, source_date: date, source_path: Path | None = None
) -> list[WorkItem]:
    """Parse the `작업완료 사항` bullet layout."""
    heading = COMPLETED_HEADING.search(text)
    if heading is None:
        return []

    body = text[heading.end():]
    following = NEXT_TOP_SECTION.search(body)
    if following:
        body = body[: following.start()]

    line_offset = _line_offset(text, body)
    items: list[WorkItem] = []
    for index, line in enumerate(body.splitlines()):
        match = BULLET.match(line)
        if not match:
            continue

        raw_body = match.group("body")
        if _is_placeholder(raw_body):
            continue

        project = _project_of(raw_body, None)
        if project is None:
            continue

        items.append(
            WorkItem(
                summary=_clean_summary(raw_body),
                project=project,
                source_date=source_date,
                tags=_tags_of(raw_body),
                done=True,
                issue_key=_issue_key_of(raw_body),
                source_path=source_path,
                source_line=line_offset + index,
            )
        )
    return items


def _slice_projects_section(text: str, heading: re.Pattern[str]) -> str | None:
    """Return the body between a `###` heading and the next `###` heading."""
    match = heading.search(text)
    if match is None:
        return None

    body = text[match.end():]
    following = SECTION_HEADING.search(body)
    return body[: following.start()] if following else body


def _project_of(body: str, fallback: str | None) -> str | None:
    """Resolve the project name, preferring the explicit `#project/` tag."""
    tagged = PROJECT_TAG.search(body)
    if tagged:
        name = tagged.group("name")
    else:
        bracket = BRACKET_PREFIX.match(body)
        name = bracket.group("value").strip() if bracket else fallback

    if not name or name in NON_REPO_PROJECTS:
        return None
    return name


def _tags_of(body: str) -> tuple[str, ...]:
    """Collect inline tags in order, dropping bookkeeping tags."""
    without_projects = PROJECT_TAG.sub(" ", body)
    seen: list[str] = []
    for match in INLINE_TAG.finditer(without_projects):
        name = match.group("name")
        if name in IGNORED_TAGS or name in seen:
            continue
        seen.append(name)
    return tuple(seen)


def _issue_key_of(body: str) -> str | None:
    """Read the Jira issue this line is linked to, in either accepted form."""
    for pattern in (ISSUE_LINK, ISSUE_FIELD):
        match = pattern.search(body)
        if match:
            return match.group("key")
    return None


def has_issue_reference(line: str) -> bool:
    """Report whether a raw worklog line already names a Jira issue."""
    return bool(ISSUE_LINK.search(line) or ISSUE_FIELD.search(line))


def _line_offset(text: str, body: str) -> int:
    """1-based line number of the body's first line within the whole document."""
    return text[: text.index(body)].count("\n") + 1


def _clean_summary(body: str) -> str:
    """Strip tags, date fields, and leading bracket labels from a task line."""
    text = ISSUE_LINK.sub(" ", body)
    text = ISSUE_FIELD.sub(" ", text)
    text = DATE_FIELD.sub(" ", text)
    text = PROJECT_TAG.sub(" ", text)
    text = INLINE_TAG.sub(" ", text)
    while True:
        stripped = BRACKET_PREFIX.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"\s+", " ", text).strip(" -")


def _is_placeholder(body: str) -> bool:
    """Detect template lines that describe no real work."""
    return any(marker in body for marker in PLACEHOLDER_MARKERS)


def _date_from_filename(path: Path) -> date | None:
    """Read the worklog date encoded in the filename stem."""
    match = FILENAME_PATTERN.match(path.stem)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None
