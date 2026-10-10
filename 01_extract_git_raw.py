#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///

"""01_extract_git_raw.py — 여러 repo의 일별 commit 로그를 raw 마크다운으로 추출.
Usage:
    01_extract_git_raw.py [DATE]              # DATE=YYYY-MM-DD
    OUTPUT=/path/to/out.md 01_extract_git_raw.py 2026-04-07
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

# ===== 설정 =====
CONFIG_FILE = Path(
    os.environ.get("CONFIG_FILE", Path.home() / "workspace/worklog/config.yaml")
)
def _default_authors() -> list[str]:
    """WORKLOG_AUTHOR는 콤마로 여러 명 지정 가능(git은 --author를 OR로 처리)."""
    raw = os.environ.get("WORKLOG_AUTHOR")
    if not raw:
        raw = (
            subprocess.run(
                ["git", "config", "--global", "user.name"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            or os.environ.get("USER", "unknown")
        )
    return [a.strip() for a in raw.split(",") if a.strip()]


AUTHORS = _default_authors()
MAX_COMMITS_FOR_STAT = int(os.environ.get("MAX_COMMITS_FOR_STAT", "8"))


@dataclass
class Commit:
    sha: str
    subject: str
    body: str = ""
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


def load_repos(path: Path) -> list[Path]:
    if not path.is_file():
        sys.exit(f"ERROR: config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        sys.exit(f"ERROR: cannot parse {path}: {exc}")

    repos: list[Path] = []
    for entry in data.get("repos") or []:
        if isinstance(entry, str):
            raw = entry
        elif isinstance(entry, dict) and "path" in entry:
            raw = entry["path"]
        else:
            continue
        repos.append(Path(raw).expanduser())
    if not repos:
        sys.exit(f"ERROR: no repos in {path}")
    return repos


def _parse_shortstat(line: str, commit: Commit) -> None:
    # 예: " 3 files changed, 47 insertions(+), 8 deletions(-)"
    for token in line.split(","):
        token = token.strip()
        num_str, _, kind = token.partition(" ")
        try:
            num = int(num_str)
        except ValueError:
            continue
        if "file" in kind:
            commit.files_changed = num
        elif "insertion" in kind:
            commit.insertions = num
        elif "deletion" in kind:
            commit.deletions = num


def git_log(repo: Path, since: str, until: str, authors: list[str]) -> list[Commit]:
    """모든 브랜치(--all)의 commit 로그를 subject/body/shortstat까지 파싱.

    --all 로 다른 브랜치·worktree 브랜치의 커밋도 포함하며,
    git이 SHA 기준으로 중복 커밋을 자동 dedup 한다.
    """
    author_args = [f"--author={a}" for a in authors]
    # \x1f = 필드 구분, \x1e = 커밋 구분(본문 뒤에 위치)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--all",
            f"--since={since}",
            f"--until={until}",
            *author_args,
            "--no-merges",
            "--pretty=format:__COMMIT__%h%x1f%s%x1f%b%x1e",
            "--shortstat",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    commits: list[Commit] = []
    for block in result.stdout.split("__COMMIT__"):
        block = block.strip()
        if not block:
            continue
        fields, _, tail = block.partition("\x1e")
        sha, _, rest = fields.partition("\x1f")
        subject, _, body = rest.partition("\x1f")
        commit = Commit(sha=sha.strip(), subject=subject.strip(), body=body.strip())
        for line in tail.splitlines():
            line = line.strip()
            if "files changed" in line or "file changed" in line:
                _parse_shortstat(line, commit)
        if commit.sha:
            commits.append(commit)
    return commits


def render_repo_section(repo_name: str, commits: list[Commit]) -> str:
    lines = [f"## {repo_name} ({len(commits)} commits)", "", "```"]

    if len(commits) <= MAX_COMMITS_FOR_STAT:
        for c in commits:
            lines.append(f"{c.sha} {c.subject}")
            for body_line in c.body.splitlines():
                body_line = body_line.strip()
                if body_line:
                    lines.append(f"    {body_line}")
            if c.files_changed:
                lines.append(
                    f" {c.files_changed} files changed, "
                    f"{c.insertions} insertions(+), {c.deletions} deletions(-)"
                )
    else:
        for c in commits:
            lines.append(f"{c.sha} {c.subject}")
            for body_line in c.body.splitlines():
                body_line = body_line.strip()
                if body_line:
                    lines.append(f"    {body_line}")
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
    repos = load_repos(CONFIG_FILE)

    sections: list[str] = []
    sections.append(f"# Worklog raw {target_date}\n")
    sections.append(f"_author: {', '.join(AUTHORS)}_\n")

    for repo in repos:
        # worktree는 .git이 파일이므로 is_dir() 대신 exists()로 판정
        if not (repo / ".git").exists():
            sections.append(f"<!-- skip: {repo} (not a git repo) -->")
            continue
        commits = git_log(repo, since, until, AUTHORS)
        if not commits:
            continue
        sections.append(render_repo_section(repo.name, commits))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sections))
    print(f"wrote: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
