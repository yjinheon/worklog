#!/usr/bin/env python3

"""Sync refined daily worklog tasks into Obsidian project todo notes.

Usage:
    sync_worklog_to_obsidian.py [DATE]
    sync_worklog_to_obsidian.py 2026-05-23 --dry-run
    sync_worklog_to_obsidian.py 2026-05-23 --skip-run-worklog
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RUN_WORKLOG = SCRIPT_DIR / "run_worklog.py"
DEFAULT_VAULT_ROOT = Path("~/workspace/astro-blog").expanduser()
WORKLOG_START = "%% worklog:auto:start"
WORKLOG_END = "%% worklog:auto:end %%"


@dataclass(frozen=True)
class ParsedWorklog:
    project_tasks: list[str]
    tasks_by_project: dict[str, list[str]]
    open_tasks: list[str]
    completed_tasks: list[str]
    repository_items: list[str]


def parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def daily_note_path(target_date: date) -> Path:
    day = target_date.strftime("%Y-%m-%d")
    return Path("02.Area") / "daily-log" / f"{day}.md"


def project_todo_path(project: str) -> Path:
    return Path("01.Project") / project / f"{project}_todo.md"


def default_worklog_path(target_date: date) -> Path:
    return Path.home() / "workspace" / "worklog" / f"{target_date}.md"


def run_worklog(target_date: date, backend: str) -> None:
    result = subprocess.run(
        [str(RUN_WORKLOG), str(target_date), "--backend", backend],
        cwd=SCRIPT_DIR,
    )
    if result.returncode != 0:
        sys.exit(f"ERROR: run_worklog.py failed (exit={result.returncode})")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def extract_section(text: str, heading: str, next_heading_level: int) -> str:
    lines = text.splitlines()
    start: int | None = None
    heading_prefix = "#" * next_heading_level + " "

    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return ""

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith(heading_prefix):
            end = idx
            break
    return "\n".join(lines[start:end])


def parse_worklog(content: str) -> ParsedWorklog:
    projects = extract_section(content, "### Projects", 3)
    repositories = extract_section(content, "### Repositories", 3)

    project_tasks: list[str] = []
    tasks_by_project: dict[str, list[str]] = {}
    open_tasks: list[str] = []
    completed_tasks: list[str] = []
    current_project: str | None = None
    for line in projects.splitlines():
        stripped = line.strip()
        if stripped.startswith("#### "):
            current_project = stripped.removeprefix("#### ").strip()
            tasks_by_project.setdefault(current_project, [])
            continue
        if (
            stripped.startswith("- [x] ")
            or stripped.startswith("- [X] ")
            or "[completion::" in stripped
        ):
            project_tasks.append(stripped)
            if current_project is not None:
                tasks_by_project.setdefault(current_project, []).append(stripped)
            completed_tasks.append(stripped)
        elif stripped.startswith("- [ ] "):
            project_tasks.append(stripped)
            if current_project is not None:
                tasks_by_project.setdefault(current_project, []).append(stripped)
            open_tasks.append(stripped)

    repository_items = [
        line.strip()
        for line in repositories.splitlines()
        if line.strip().startswith("- ") and not line.strip().startswith("- [")
    ]
    return ParsedWorklog(
        project_tasks=dedupe(project_tasks),
        tasks_by_project={
            project: dedupe(tasks)
            for project, tasks in tasks_by_project.items()
            if tasks
        },
        open_tasks=dedupe(open_tasks),
        completed_tasks=dedupe(completed_tasks),
        repository_items=dedupe(repository_items),
    )


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = normalize_task_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def normalize_task_key(value: str) -> str:
    value = re.sub(r"^- \[[ xX]\]\s*", "", value)
    value = re.sub(r"\[due::[^\]]+\]", "", value)
    value = re.sub(r"\[completion::[^\]]+\]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().lower()


def build_worklog_block(target_date: date, parsed: ParsedWorklog) -> str:
    tasks = parsed.project_tasks
    if not tasks:
        return ""

    lines = [f"{WORKLOG_START} date={target_date} %%"]
    lines.extend(dedupe(tasks))
    lines.append(WORKLOG_END)
    return "\n".join(lines)


def build_project_block(target_date: date, project: str, tasks: list[str]) -> str:
    if not tasks:
        return ""
    lines = [f"{WORKLOG_START} date={target_date} project={project} %%"]
    lines.extend(dedupe(tasks))
    lines.append(WORKLOG_END)
    return "\n".join(lines)


def previous_day_link(target_date: date) -> str:
    return str(target_date - timedelta(days=1))


def next_day_link(target_date: date) -> str:
    return str(target_date + timedelta(days=1))


def new_daily_note(target_date: date) -> str:
    day = str(target_date)
    return f"""---
id: daily-log-template
aliases: []
tags:
  - template
generated: {day}
created: {day}
---

# {day}

## 01. TODO LIST

### Today

**오늘 작성한  할일**

- [ ] #todo Workout #health [due:: today]

### Due Today

```dataview
TASK
where !completed
and due = date(today)
sort due asc
```

### Due This Week

```dataview
TASK
where !completed
and due >= date(today) - dur(1 week)
and due <= date(today) + dur(1 week)
sort due asc
```

## Work

## Personal

## Thoughts

[[{previous_day_link(target_date)}|< yesterday]] | [[{next_day_link(target_date)}|tomorrow >]]

---
"""


def ensure_todo_list_section(content: str, target_date: date) -> str:
    if "### Today" in content:
        return content

    todo_block = """## 01. TODO LIST

### Today

**오늘 작성한  할일**

- [ ] #todo Workout #health [due:: today]

### Due Today

```dataview
TASK
where !completed
and due = date(today)
sort due asc
```

"""
    title = f"# {target_date}"
    if title in content:
        return content.replace(title, f"{title}\n\n{todo_block.rstrip()}", 1)
    return f"{todo_block}\n{content}"


def replace_or_insert_auto_block(today_body: str, block: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(WORKLOG_START)}[^\n]*\n.*?\n{re.escape(WORKLOG_END)}\n?",
        re.DOTALL,
    )
    if pattern.search(today_body):
        return pattern.sub(f"\n{block}\n", today_body).strip("\n")

    lines = today_body.splitlines()
    insert_at: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("- ["):
            insert_at = idx + 1

    if insert_at is None:
        for idx, line in enumerate(lines):
            if "오늘 작성한" in line:
                insert_at = idx + 1
                break

    if insert_at is None:
        insert_at = len(lines)

    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1

    new_lines = lines[:insert_at]
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.extend(block.splitlines())
    if insert_at < len(lines):
        new_lines.append("")
        new_lines.extend(lines[insert_at:])
    return "\n".join(new_lines).strip("\n")


def remove_auto_block(content: str, *, target_date: date | None = None) -> str:
    date_part = "" if target_date is None else rf"[^\n]*date={re.escape(str(target_date))}"
    pattern = re.compile(
        rf"\n?{re.escape(WORKLOG_START)}{date_part}[^\n]*\n.*?\n{re.escape(WORKLOG_END)}\n?",
        re.DOTALL,
    )
    return pattern.sub("\n", content).rstrip() + "\n"


def sync_today_section(content: str, target_date: date, block: str) -> str:
    if not block:
        return content

    content = ensure_todo_list_section(content, target_date)
    lines = content.splitlines()

    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "### Today":
            start = idx
            break
    if start is None:
        raise ValueError("cannot find or create ### Today section")

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("### "):
            end = idx
            break

    section_body = "\n".join(lines[start + 1 : end])
    new_body = replace_or_insert_auto_block(section_body, block)
    new_body_lines = new_body.splitlines()
    if new_body_lines and new_body_lines[0].strip():
        new_body_lines.insert(0, "")
    if new_body_lines and new_body_lines[-1].strip():
        new_body_lines.append("")
    new_lines = lines[: start + 1] + new_body_lines + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"


def new_project_todo(project: str) -> str:
    today = date.today().isoformat()
    return f"""---
created: {today}
updated: {today}
---

# {project}

## Worklog
"""


def ensure_worklog_section(content: str) -> str:
    if re.search(r"^## Worklog\s*$", content, re.MULTILINE):
        return content

    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            end += len("\n---")
            return content[:end].rstrip() + "\n\n## Worklog\n" + content[end:].lstrip()

    return content.rstrip() + "\n\n## Worklog\n"


def sync_project_todo(content: str, target_date: date, project: str, block: str) -> str:
    content = ensure_worklog_section(content)
    lines = content.splitlines()

    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "## Worklog":
            start = idx
            break
    if start is None:
        raise ValueError("cannot find or create ## Worklog section")

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break

    section_body = "\n".join(lines[start + 1 : end])
    new_body = replace_or_insert_auto_block(section_body, block)
    new_body_lines = new_body.splitlines()
    if new_body_lines and new_body_lines[0].strip():
        new_body_lines.insert(0, "")
    if new_body_lines and new_body_lines[-1].strip():
        new_body_lines.append("")
    new_lines = lines[: start + 1] + new_body_lines + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"


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
        choices=["claude", "gemini", "auto"],
        default="auto",
        help="LLM backend for run_worklog.py (default: auto).",
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
        help="Refined worklog markdown path. Defaults to ~/workspace/worklog/DATE.md.",
    )
    parser.add_argument(
        "--skip-run-worklog",
        action="store_true",
        help="Use an existing refined worklog file without running run_worklog.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the note diff instead of writing changes.",
    )
    parser.add_argument(
        "--daily-note",
        action="store_true",
        help="Also sync the full worklog block into the daily note Today section.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    target_date = parse_date(args.date)

    if not args.skip_run_worklog:
        run_worklog(target_date, args.backend)

    worklog_path = (args.worklog_path or default_worklog_path(target_date)).expanduser()
    if not worklog_path.is_file():
        sys.exit(f"ERROR: worklog not found: {worklog_path}")

    parsed = parse_worklog(read_text(worklog_path))
    if not parsed.project_tasks:
        print(f"no worklog tasks found for {target_date}")
        return 0

    vault_root = args.vault_root.expanduser()
    changes: list[tuple[Path, Path, str, str]] = []

    for project, tasks in parsed.tasks_by_project.items():
        block = build_project_block(target_date, project, tasks)
        relative_path = project_todo_path(project)
        path = vault_root / relative_path
        before = read_text(path) if path.is_file() else new_project_todo(project)
        after = sync_project_todo(before, target_date, project, block)
        if before != after:
            changes.append((relative_path, path, before, after))

    relative_note_path = daily_note_path(target_date)
    note_path = vault_root / relative_note_path
    if note_path.is_file():
        before = read_text(note_path)
        if args.daily_note:
            block = build_worklog_block(target_date, parsed)
            after = sync_today_section(before, target_date, block)
        else:
            after = remove_auto_block(before, target_date=target_date)
        if before != after:
            changes.append((relative_note_path, note_path, before, after))
    elif args.daily_note:
        block = build_worklog_block(target_date, parsed)
        before = new_daily_note(target_date)
        after = sync_today_section(before, target_date, block)
        if before != after:
            changes.append((relative_note_path, note_path, before, after))

    if not changes:
        print("no changes")
        return 0

    if args.dry_run:
        for relative_path, _, before, after in changes:
            sys.stdout.write(unified_diff(before, after, relative_path))
        return 0

    for _, path, _, after in changes:
        write_text(path, after)
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
