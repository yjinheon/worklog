#!/usr/bin/env python3

"""daily_worklog.py — 여러 repo의 일별 commit 로그를 raw 마크다운으로 추출.

Usage:
    daily_worklog.py [DATE]              # DATE=YYYY-MM-DD, 기본=오늘
    OUTPUT=/path/to/out.md daily_worklog.py 2026-04-07
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# ===== 설정 =====
REPOS_FILE = Path(
    os.environ.get("REPOS_FILE", Path.home() / "workspace/worklog/repos.conf")
)
AUTHOR = os.environ.get("WORKLOG_AUTHOR") or (
    subprocess.run(
        ["git", "config", "--global", "user.name"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    or os.environ.get("USER", "unknown")
)
MAX_COMMITS_FOR_STAT = int(os.environ.get("MAX_COMMITS_FOR_STAT", "8"))


@dataclass
class Commit:
    sha: str
    subject: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


def load_repos(path: Path) -> list[Path]:
    if not path.is_file():
        sys.exit(f"ERROR: repos file not found: {path}")
    repos: list[Path] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        repos.append(Path(line).expanduser())
    if not repos:
        sys.exit(f"ERROR: no repos in {path}")
    return repos


def git_log(repo: Path, since: str, until: str, author: str) -> list[Commit]:
    """git log --shortstat 결과를 파싱해 Commit 리스트로 반환."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            f"--since={since}",
            f"--until={until}",
            f"--author={author}",
            "--no-merges",
            "--pretty=format:__COMMIT__%h %s",
            "--shortstat",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    commits: list[Commit] = []
    current: Commit | None = None

    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            if current is not None:
                commits.append(current)
            payload = line[len("__COMMIT__") :]
            sha, _, subject = payload.partition(" ")
            current = Commit(sha=sha, subject=subject)
        elif "files changed" in line or "file changed" in line:
            # 예: " 3 files changed, 47 insertions(+), 8 deletions(-)"
            if current is None:
                continue
            for token in line.split(","):
                token = token.strip()
                num_str, _, kind = token.partition(" ")
                try:
                    num = int(num_str)
                except ValueError:
                    continue
                if "file" in kind:
                    current.files_changed = num
                elif "insertion" in kind:
                    current.insertions = num
                elif "deletion" in kind:
                    current.deletions = num
    if current is not None:
        commits.append(current)
    return commits


def render_repo_section(repo_name: str, commits: list[Commit]) -> str:
    lines = [f"## {repo_name} ({len(commits)} commits)", "", "```"]

    if len(commits) <= MAX_COMMITS_FOR_STAT:
        for c in commits:
            lines.append(f"{c.sha} {c.subject}")
            if c.files_changed:
                lines.append(
                    f" {c.files_changed} files changed, "
                    f"{c.insertions} insertions(+), {c.deletions} deletions(-)"
                )
    else:
        for c in commits:
            lines.append(f"{c.sha} {c.subject}")
        total_files = sum(c.files_changed for c in commits)
        total_ins = sum(c.insertions for c in commits)
        total_del = sum(c.deletions for c in commits)
        lines.append("--- summary ---")
        lines.append(
            f"files={total_files} insertions={total_ins} deletions={total_del}"
        )

    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    target_date = (
        datetime.strptime(argv[1], "%Y-%m-%d").date() if len(argv) > 1 else date.today()
    )
    since = f"{target_date} 00:00:00"
    until = f"{target_date} 23:59:59"

    output_path = Path(os.environ.get("OUTPUT", f"/tmp/worklog-{target_date}.raw.md"))
    repos = load_repos(REPOS_FILE)

    sections: list[str] = []
    sections.append(f"# Worklog raw {target_date}\n")
    sections.append(f"_author: {AUTHOR}_\n")

    for repo in repos:
        if not (repo / ".git").is_dir():
            sections.append(f"<!-- skip: {repo} (not a git repo) -->")
            continue
        commits = git_log(repo, since, until, AUTHOR)
        if not commits:
            continue
        sections.append(render_repo_section(repo.name, commits))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sections))
    print(f"wrote: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
