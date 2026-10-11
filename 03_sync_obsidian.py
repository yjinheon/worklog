#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///

"""Sync a worklog into the Obsidian daily note.

daily note가 원본이다:
  - ``### Today`` auto 블록  — 작업 줄 (``worklog_parser`` / 커밋 워크로그 파생)
  - ``## 03. Work`` report 블록 — 그날 회사 업무일지 본문 그대로

01.Project에는 작업 줄·Highlights를 복사하지 않는다. 유일한 쓰기는
존재하지 않는 새 프로젝트 노트 생성이다(아카이브에 있는 프로젝트는 재생성하지
않음).

Usage:
    03_sync_obsidian.py [DATE]
    03_sync_obsidian.py 2026-10-08 --skip-run-worklog --worklog-path worklogs/2026-10-08.md
    03_sync_obsidian.py 2026-09-15 --commit-worklog /tmp/worklog-2026-09-15.md --dry-run
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
RUN_WORKLOG = SCRIPT_DIR / "00_run_commit_worklog.py"
DEFAULT_VAULT_ROOT = Path("~/workspace/astro-blog").expanduser()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def _load_config() -> dict:
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raw = {}
    task_tags = set(raw.get("task_tags") or [
        "feat", "fix", "refactor", "perf", "docs", "test", "chore", "build", "ci", "style", "revert"
    ])
    repos: dict[str, str] = {}
    for repo in raw.get("repos") or []:
        try:
            folder = Path(repo["path"].replace("~", Path.home())).name
        except (KeyError, TypeError):
            continue
        repos[folder] = repo.get("group") or ""
    return {"task_tags": task_tags, "repos": repos}


_CONFIG = _load_config()
TASK_TAGS = _CONFIG["task_tags"]
# 수집 대상 repo 폴더명만 프로젝트 노트 대상이다 (옛 LLM 이름은 제외)
KNOWN_PROJECTS = set(_CONFIG["repos"])

DAILY_LOG_DIR = Path("02.Area") / "daily-log"
DAILY_TEMPLATE = Path("02.Area") / "template" / "daily-log-template.md"
PROJECT_DIR = Path("01.Project")
ARCHIVE_DIR = Path("04.Archive") / "archived_project"

AUTO_START = "%% worklog:auto:start"
AUTO_END = "%% worklog:auto:end %%"
REPORT_START = "%% worklog:report:start"
REPORT_END = "%% worklog:report:end %%"


_daily_template_cache: str | None = None


@dataclass(frozen=True)
class ParsedWorklog:
    project_tasks: list[str] = field(default_factory=list)
    tasks_by_project: dict[str, list[str]] = field(default_factory=dict)
    highlights_by_project: dict[str, list[str]] = field(default_factory=dict)


def parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_worklog(target_date: date, backend: str) -> None:
    result = subprocess.run(
        [str(RUN_WORKLOG), str(target_date), "--backend", backend],
        cwd=SCRIPT_DIR,
    )
    if result.returncode != 0:
        sys.exit(f"ERROR: 00_run_commit_worklog.py failed (exit={result.returncode})")


def _section_bounds(
    content: str, heading: str, ignore_worklog_blocks: bool = False
) -> tuple[int, int] | None:
    """(first line after heading, line index where the section ends).

    The heading may carry a trailing colon. The section ends at the next
    heading of the same-or-higher level — unless it sits inside a
    ``%% worklog:*:start`` … ``%% worklog:*:end`` block and
    ``ignore_worklog_blocks`` is set (report bodies may contain headings).
    """
    level = len(heading) - len(heading.lstrip("#"))
    stop_prefix = "#" * level + " "
    lines = content.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() in (heading, heading + ":"):
            start = idx + 1
            break
    if start is None:
        return None
    end = len(lines)
    in_block = False
    for idx in range(start, len(lines)):
        line = lines[idx]
        if ignore_worklog_blocks:
            if not in_block:
                if "%% worklog:" in line and ":start" in line:
                    in_block = True
                elif line.startswith(stop_prefix):
                    end = idx
                    break
            elif "%% worklog:" in line and ":end" in line:
                in_block = False
        elif line.startswith(stop_prefix):
            end = idx
            break
    return start, end


def _section_body(content: str, heading: str, ignore_worklog_blocks: bool = False) -> str:
    """Body between a heading line and the next heading of the same-or-higher level."""
    bounds = _section_bounds(content, heading, ignore_worklog_blocks)
    if bounds is None:
        return ""
    start, end = bounds
    return "\n".join(content.splitlines()[start:end])


# ---------------------------------------------------------------------------
# worklog → 작업 줄 / report 본문
# ---------------------------------------------------------------------------


def _task_type_of(tags: tuple[str, ...], brackets: str | None) -> str:
    if brackets:
        first = brackets.split(",")[0].strip().lstrip("#").strip()
        if first in TASK_TAGS:
            return first
    for tag in tags:
        if tag in TASK_TAGS:
            return tag
    return "chore"


def render_task_line(item, target_date: date, bracket_types: str | None = None) -> str:
    state = "x" if item.done else " "
    type_tag = _task_type_of(item.tags, bracket_types)
    line = f"- [{state}] {item.summary} #project/{item.project} #todo #{type_tag}"
    line += f" [due:: {target_date}]"
    if item.done:
        line += f" [completion:: {target_date}]"
    if item.issue_key:
        line += f" [issue:: {item.issue_key}]"
    return line


def _bracket_types(raw_line: str) -> str | None:
    """T 형식 원본 줄 `[project] [type, …]` 두 번째 필드."""
    match = re.match(r"\[[^\]]+\]\s*\[([^\]]+)\]", raw_line)
    return match.group(1) if match else None


def _normalize_task_key(value: str) -> str:
    value = re.sub(r"^- \[[ xX]\]\s*", "", value)
    value = re.sub(r"\[(?:due|completion|issue)::[^\]]+\]", "", value)
    return re.sub(r"\s+", " ", value).strip().lower()




def parse_worklog(
    original: str,
    target_date: date,
    commit_worklog: str | None = None,
) -> ParsedWorklog:
    from worklog_parser import parse_worklog as parse_by_file

    source_text = original
    items = []
    if original.strip():
        items = parse_by_file(original, target_date)
    if not items and commit_worklog and commit_worklog.strip():
        source_text = commit_worklog
        items = parse_by_file(commit_worklog, target_date)

    source_lines = source_text.splitlines()

    lines: list[str] = []
    by_project: dict[str, list[str]] = {}
    seen: set[str] = set()
    for item in items:
        raw = source_lines[item.source_line - 1] if item.source_line else ""
        line = render_task_line(item, target_date, _bracket_types(raw.strip()))
        key = _normalize_task_key(line)
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        by_project.setdefault(item.project, []).append(line)
    highlights: dict[str, list[str]] = {}
    if original.strip():
        for project, bullets in _highlights_by_project(original).items():
            highlights[project] = [f"- #highlight #project/{project} {b}" for b in bullets]
    return ParsedWorklog(
        project_tasks=lines,
        tasks_by_project=by_project,
        highlights_by_project=highlights,
    )


def _highlights_by_project(text: str) -> dict[str, list[str]]:
    """`### Highlights` / `#### <project>` / `- …` → project별 본문(줄 빼고)."""
    body = _section_body(text, "### Highlights")
    if not body:
        return {}
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#### "):
            current = stripped.removeprefix("#### ").strip()
        elif current and stripped.startswith("- ") and not stripped.startswith("- ["):
            out.setdefault(current, []).append(stripped.removeprefix("- ").strip())
    return out


def report_body(content: str) -> str:
    """Work report 블록에 들어갈 회사 업무일지 본문.

    - `---` 아래(커밋 워크로그)는 제외
    - T 형식(`작업완료 사항`)은 그 섹션까지의 본문만: 불릿은 이미 작업 줄로 들어간다
    - P 형식(`### Projects`)은 `### Summary`(와 있으면 `### Notes`)만
    """
    if not content.strip():
        return ""

    sep = content.find("\n---\n")
    upper = content[:sep].rstrip() if sep != -1 else content
    # 원문 Highlights는 build_report_block이 #highlight 줄로 붙이므로 본문에서는 뺀다
    upper = re.split(r"^###\s+Highlights\s*$", upper, maxsplit=1, flags=re.MULTILINE)[0].rstrip()
    has_projects = re.search(r"^###\s+Projects\s*:?\s*$", content, re.MULTILINE)

    if has_projects and sep == -1:
        # P 형식 옛 파일(반이 전부 마크다운): 작업 줄은 Already Today로 들어감
        parts = [_section_body(content, "### Summary")]
        notes = _section_body(content, "### Notes")
        if notes.strip():
            parts.append(notes)
        return "\n\n".join(part.strip("\n") for part in parts if part.strip()).strip()

    if not has_projects:
        # 평문 파일(T/R): T 형식 작업완료 불릿(작업 줄로 이미 넘어감)만 잘라냄
        match = re.search(r"^작업(완료\s*)?사항\s*:?\s*$", upper, re.MULTILINE)
        if match:
            tail = upper[match.start():]
            if re.search(r"^\s*-\s+\[[^\]]+\]\s*\[[^\]]+\]", tail, re.MULTILINE):
                upper = upper[:match.start()].rstrip()
        return upper.strip()

    # 10-08: 위가 회사 본문의 반, 아래가 커밋 워크로그 마크다운
    return upper.strip()


def build_report_block(target_date: date, content: str, highlights: dict[str, list[str]]) -> str:
    if not content.strip():
        return ""
    lines = [f"{REPORT_START} date={target_date} %%"]
    lines.append(content.strip("\n"))
    if highlights:
        lines.append("")
        for project, bullets in highlights.items():
            for bullet in bullets:
                lines.append(bullet)
    lines.append(REPORT_END)
    return "\n".join(lines)


def build_task_block(target_date: date, tasks: list[str]) -> str:
    if not tasks:
        return ""
    lines = [f"{AUTO_START} date={target_date} %%"]
    lines.extend(tasks)
    lines.append(AUTO_END)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# daily note
# ---------------------------------------------------------------------------


def default_daily_note_path(target_date: date) -> Path:
    return DAILY_LOG_DIR / f"{target_date}.md"


def load_daily_template(vault_root: Path) -> str:
    global _daily_template_cache
    if _daily_template_cache is None:
        path = vault_root / DAILY_TEMPLATE
        if not path.is_file():
            sys.exit(f"ERROR: daily note template not found: {path}")
        _daily_template_cache = read_text(path)
    return _daily_template_cache


def new_daily_note(vault_root: Path, target_date: date) -> str:
    template = load_daily_template(vault_root)
    day = str(target_date)
    template = template.replace("{{date}}", day)
    template = template.replace("{{yesterday}}", str(target_date - timedelta(days=1)))
    template = template.replace("{{tomorrow}}", str(target_date + timedelta(days=1)))
    return template


def resolve_daily_note(vault_root: Path, target_date: date) -> Path:
    """기존 노트는 하위 폴더(`2026/2026-MM/`)에 있을 수 있다."""
    day = f"{target_date}.md"
    flat = vault_root / DAILY_LOG_DIR / day
    if flat.is_file():
        return flat
    for match in sorted((vault_root / DAILY_LOG_DIR).rglob(day)):
        if match.is_file():
            return match
    return flat


def _replace_or_insert_block(section_body: str, block: str, start_marker: str) -> str:
    """같은 start 마커(date 없이)의 블록을 교체. 없으면 섹션 끝에 넣는다."""
    first = block.splitlines()[0]
    pattern = re.compile(
        rf"\n?{re.escape(start_marker)}[^\n]*\n.*?\n{re.escape(AUTO_END if start_marker == AUTO_START else REPORT_END)}\n?",
        re.DOTALL,
    )
    if pattern.search(section_body):
        return pattern.sub(lambda _: f"\n{block}\n", section_body).strip("\n")
    body = section_body.strip("\n")
    if not body:
        return block
    return f"{body}\n\n{block}"


def ensure_daily_sections(content: str) -> str:
    if "### Today" not in content:
        content = re.sub(
            r"^## 01\. TODO LIST\s*$",
            "## 01. TODO LIST\n\n### Today\n\n**오늘 작성한  할일**\n",
            content,
            count=1,
        )
    if not re.search(r"^## 03\. Work\s*$", content, re.MULTILINE):
        content += "\n## 03. Work\n"
    return content


def sync_daily_note(
    content: str, target_date: date, task_block: str, report_block: str
) -> str:
    content = ensure_daily_sections(content)

    if task_block:
        today = _section_body(content, "### Today", ignore_worklog_blocks=True)
        content = _splice_section(
            content, "### Today", _replace_or_insert_block(today, task_block, AUTO_START)
        )

    if report_block:
        work = _section_body(content, "## 03. Work", ignore_worklog_blocks=True)
        content = _splice_section(
            content, "## 03. Work", _replace_or_insert_block(work, report_block, REPORT_START)
        )

    return content


def _splice_section(content: str, heading: str, new_body: str) -> str:
    lines = content.splitlines()
    bounds = _section_bounds(content, heading, ignore_worklog_blocks=True)
    if bounds is None:
        raise ValueError(f"cannot find section: {heading}")
    start, end = bounds
    body_lines = new_body.splitlines()
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    if body_lines:
        spliced = ["", *body_lines, ""]
    else:
        spliced = []
    return "\n".join(lines[: start + 1] + spliced + lines[end:]).rstrip() + "\n"


# ---------------------------------------------------------------------------
# project note — 존재하지 않을 때만 생성
# ---------------------------------------------------------------------------


def project_note_path(project: str) -> Path:
    return PROJECT_DIR / project / f"{project}_todo.md"


def find_project_note(vault_root: Path, project: str) -> Path:
    standard = vault_root / project_note_path(project)
    if standard.is_file():
        return standard
    fallback = vault_root / PROJECT_DIR / project / "todo.md"
    if fallback.is_file():
        return fallback
    return standard


def is_archived_project(vault_root: Path, project: str) -> bool:
    return (vault_root / ARCHIVE_DIR / project).is_dir()


def new_project_todo(project: str, today: date | None = None) -> str:
    """01.Project/workspace/workspace_todo.md와 동일한 구조 + 조회 블록."""
    day = str(today or date.today())
    tag = f"#project/{project}"
    return f"""---
title: {project}
status: IN_PROGRESS
tag:
  - project/{project}
created: {day}
updated: {day}
---

## Goal

- 뭔가 프로젝트 목표적기

```dataview
TABLE
  T.text AS "작업 내용",
  T.due AS "마감일",
  T.completion AS "완료일",
  choice(T.completed, "완료", "진행중") AS "상태"
FROM {tag}
FLATTEN file.tasks AS T
WHERE contains(T.tags, "{tag}")
SORT T.completion DESC
```

## Files

```dataview
TABLE
alias AS "Description" , dateformat(updated,"yyyy-MM-dd") as updated
from {tag}
SORT file.path ASCENDING
```

## Last Update

```dataviewjs
const cfile = dv.current().file;
const list = dv.pages(`"${{cfile.folder}}"`)
    .filter(b => b.file.name != cfile.name)
    .sort(b => b.file.cday)
    .map(b => b.file.link);
dv.list(list);
```

## Todo

```dataview
list from {tag}
where contains(status,"todo")
SORT file.path ASCENDING
```

## In Progress

```dataview
list from {tag}
where contains(status,"IN_PROGRESS")
SORT file.path ASCENDING
```

## Done

```dataview
list from {tag}
where contains(status,"DONE")
SORT file.path ASCENDING
```

## Reference

## Worklog

```dataview
TABLE WITHOUT ID L.text AS "Highlight", file.link AS "날짜"
FROM #highlight AND {tag}
FLATTEN file.lists AS L
WHERE contains(L.tags, "#highlight") AND contains(L.tags, "{tag}")
SORT file.name DESC
```
"""


def unified_diff(before: str, after: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        )
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "date",
        nargs="?",
        help="Target date (YYYY-MM-DD), defaults to today.",
    )
    parser.add_argument(
        "--backend",
        "-b",
        choices=["claude", "gemini", "kimi", "codex", "litellm", "auto"],
        default="auto",
        help="LLM backend for 00_run_commit_worklog.py (default: auto).",
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=DEFAULT_VAULT_ROOT,
        help=f"Obsidian vault root (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--worklog-path",
        type=Path,
        default=None,
        help="Refined worklog markdown path. Defaults to ~/workspace/worklog/DATE.md "
        "if it exists, otherwise 00_run_commit_worklog.py is run first.",
    )
    parser.add_argument(
        "--commit-worklog",
        type=Path,
        default=None,
        help="P-format commit worklog (e.g. /tmp/worklog-DATE.md) to parse for task "
        "lines when the original worklog file has none.",
    )
    parser.add_argument(
        "--skip-run-worklog",
        action="store_true",
        help="Use an existing worklog file without running 00_run_commit_worklog.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the note diff instead of writing changes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    target_date = parse_date(args.date)

    if args.worklog_path is not None:
        worklog_path = args.worklog_path.expanduser()
    else:
        candidate = SCRIPT_DIR / "worklogs" / f"{target_date}.md"
        if not candidate.is_file():
            candidate = Path.home() / "workspace" / "worklog" / f"{target_date}.md"
        if candidate.is_file():
            worklog_path = candidate
        elif not args.skip_run_worklog:
            run_worklog(target_date, args.backend)
            worklog_path = Path.home() / "workspace" / "worklog" / f"{target_date}.md"
        else:
            sys.exit(f"ERROR: worklog not found: {candidate}")

    original = read_text(worklog_path) if worklog_path.is_file() else ""
    commit_text = ""
    if args.commit_worklog is not None and args.commit_worklog.is_file():
        commit_text = read_text(args.commit_worklog)
    parsed = parse_worklog(original, target_date, commit_worklog=commit_text or None)

    vault_root = args.vault_root.expanduser()
    changes: list[tuple[Path, str]] = []


    # 새 프로젝트 노트 생성 (config repo에 없고 아카이브에 있는 프로젝트는 제외)
    projects = list(dict.fromkeys([*parsed.tasks_by_project, *parsed.highlights_by_project]))
    for project in projects:
        if project not in KNOWN_PROJECTS:
            continue
        if is_archived_project(vault_root, project):
            continue
        if not find_project_note(vault_root, project).is_file():
            changes.append((project_note_path(project), new_project_todo(project, target_date)))

    note_path = resolve_daily_note(vault_root, target_date)
    before = read_text(note_path) if note_path.is_file() else new_daily_note(vault_root, target_date)
    after = sync_daily_note(
        before,
        target_date,
        build_task_block(target_date, parsed.project_tasks),
        build_report_block(target_date, report_body(original), parsed.highlights_by_project),
    )
    if before != after:
        changes.append((note_path.relative_to(vault_root), after))

    if not changes:
        print("no changes")
        return 0

    if args.dry_run:
        for relative_path, after in changes:
            path = vault_root / relative_path
            before_text = read_text(path) if path.is_file() else ""
            sys.stdout.write(unified_diff(before_text, after, relative_path))
        return 0

    for relative_path, after in changes:
        path = vault_root / relative_path
        write_text(path, after)
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
