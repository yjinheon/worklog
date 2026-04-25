#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///
"""collect.py — scan registered repos and upsert recent commits + TODOs into SQLite.

Usage:
    collect.py                       # use default config + default DB
    collect.py --config PATH         # alternate config.yaml
    collect.py --discover            # also scan ~/workspace/*/.git
    collect.py --all-authors         # disable own-author filter
    collect.py --db PATH             # alternate SQLite location
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path.home() / "workspace/worklog/config.yaml"
DEFAULT_DISCOVER_GLOB = Path.home() / "workspace"
COMMITS_PER_REPO = 5
TODO_NAME_PATTERNS = ("pr_todo.md", "TODO.md")
TODO_GLOB_PATTERN = re.compile(r"todo", re.IGNORECASE)
CHECKBOX_RE = re.compile(r"^\s*- \[( |x|X)\]")


@dataclass
class RepoEntry:
    name: str
    path: Path
    project: str | None


@dataclass
class Commit:
    sha: str
    ts: int
    author: str
    subject: str


@dataclass
class TodoFile:
    rel_path: str
    open_count: int
    done_count: int
    total_lines: int
    sample: str


def default_db_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state_home) / "next-task-suggester" / "cache.db"


def resolve_author() -> str:
    explicit = os.environ.get("WORKLOG_AUTHOR")
    if explicit:
        return explicit
    res = subprocess.run(
        ["git", "config", "--global", "user.name"],
        capture_output=True,
        text=True,
    )
    name = res.stdout.strip()
    return name or os.environ.get("USER", "unknown")


def load_repos(config_path: Path) -> list[RepoEntry]:
    if not config_path.is_file():
        sys.exit(f"ERROR: config file not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        sys.exit(f"ERROR: cannot parse {config_path}: {exc}")

    out: list[RepoEntry] = []
    seen: set[Path] = set()
    for entry in data.get("repos") or []:
        if isinstance(entry, str):
            raw, project = entry, None
        elif isinstance(entry, dict) and "path" in entry:
            raw, project = entry["path"], entry.get("project")
        else:
            continue
        path = Path(raw).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        out.append(RepoEntry(name=path.name, path=path, project=project))
    return out


def discover_repos(root: Path, exclude: set[Path]) -> list[RepoEntry]:
    if not root.is_dir():
        return []
    out: list[RepoEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / ".git").is_dir():
            continue
        resolved = child.resolve()
        if resolved in exclude:
            continue
        out.append(RepoEntry(name=resolved.name, path=resolved, project=None))
    return out


def git_recent_commits(
    repo: Path, n: int, author: str | None
) -> list[Commit]:
    if not (repo / ".git").is_dir():
        return []
    cmd = [
        "git",
        "-C",
        str(repo),
        "log",
        f"-n{n}",
        "--no-merges",
        "--pretty=format:%H%x09%ct%x09%an%x09%s",
    ]
    if author:
        cmd.insert(5, f"--author={author}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return []
    commits: list[Commit] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        sha, ts, an, subject = parts
        try:
            ts_int = int(ts)
        except ValueError:
            continue
        commits.append(Commit(sha=sha, ts=ts_int, author=an, subject=subject))
    return commits


def git_branch(repo: Path) -> str | None:
    res = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    name = res.stdout.strip()
    return name or None


def find_todo_files(repo: Path) -> list[Path]:
    candidates: list[Path] = []
    for depth_root in (repo, *(p for p in repo.iterdir() if p.is_dir() and not p.name.startswith("."))):
        if not depth_root.is_dir():
            continue
        try:
            for child in depth_root.iterdir():
                if not child.is_file():
                    continue
                if child.suffix.lower() != ".md":
                    continue
                name = child.name
                if name in TODO_NAME_PATTERNS or TODO_GLOB_PATTERN.search(name):
                    candidates.append(child)
        except (PermissionError, OSError):
            continue
        # only descend one level
        if depth_root is repo:
            continue
    # de-dup while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        unique.append(c)
    return unique


def parse_todo_file(repo: Path, todo_path: Path) -> TodoFile:
    try:
        text = todo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return TodoFile(
            rel_path=str(todo_path.relative_to(repo)),
            open_count=0,
            done_count=0,
            total_lines=0,
            sample="",
        )
    open_lines: list[str] = []
    open_count = 0
    done_count = 0
    total_lines = 0
    for raw in text.splitlines():
        total_lines += 1
        m = CHECKBOX_RE.match(raw)
        if not m:
            continue
        marker = m.group(1)
        if marker == " ":
            open_count += 1
            if len(open_lines) < 3:
                open_lines.append(raw.strip())
        else:
            done_count += 1
    return TodoFile(
        rel_path=str(todo_path.relative_to(repo)),
        open_count=open_count,
        done_count=done_count,
        total_lines=total_lines,
        sample="\n".join(open_lines),
    )


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repo (
          name        TEXT PRIMARY KEY,
          path        TEXT NOT NULL,
          project     TEXT,
          branch      TEXT,
          last_seen   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commit_log (
          repo        TEXT NOT NULL,
          sha         TEXT NOT NULL,
          ts          INTEGER NOT NULL,
          author      TEXT,
          subject     TEXT NOT NULL,
          PRIMARY KEY (repo, sha)
        );
        CREATE INDEX IF NOT EXISTS idx_commit_repo_ts
          ON commit_log(repo, ts DESC);
        CREATE TABLE IF NOT EXISTS todo (
          repo         TEXT NOT NULL,
          file         TEXT NOT NULL,
          open_count   INTEGER NOT NULL,
          done_count   INTEGER NOT NULL,
          total_lines  INTEGER NOT NULL,
          sample       TEXT,
          PRIMARY KEY (repo, file)
        );
        CREATE TABLE IF NOT EXISTS collect_run (
          ts                INTEGER PRIMARY KEY,
          repo_count        INTEGER NOT NULL,
          ok_count          INTEGER NOT NULL,
          new_commit_repos  TEXT
        );
        """
    )
    conn.commit()
    return conn


def collect(
    config_path: Path,
    db_path: Path,
    discover: bool,
    all_authors: bool,
) -> dict:
    repos = load_repos(config_path)
    if discover:
        repos.extend(
            discover_repos(DEFAULT_DISCOVER_GLOB, exclude={r.path for r in repos})
        )

    author = None if all_authors else resolve_author()
    now = int(time.time())

    conn = init_db(db_path)
    new_commit_repos: list[str] = []
    ok_count = 0

    try:
        with conn:
            for entry in repos:
                if not entry.path.exists() or not (entry.path / ".git").is_dir():
                    continue
                ok_count += 1
                commits = git_recent_commits(
                    entry.path, COMMITS_PER_REPO, author
                )
                branch = git_branch(entry.path)

                # detect "new" commits vs existing rows
                if commits:
                    existing = {
                        row[0]
                        for row in conn.execute(
                            "SELECT sha FROM commit_log WHERE repo=?",
                            (entry.name,),
                        )
                    }
                    if any(c.sha not in existing for c in commits):
                        new_commit_repos.append(entry.name)

                conn.execute(
                    """
                    INSERT INTO repo(name, path, project, branch, last_seen)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                      path=excluded.path,
                      project=excluded.project,
                      branch=excluded.branch,
                      last_seen=excluded.last_seen
                    """,
                    (entry.name, str(entry.path), entry.project, branch, now),
                )
                for c in commits:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO commit_log(
                          repo, sha, ts, author, subject
                        ) VALUES(?, ?, ?, ?, ?)
                        """,
                        (entry.name, c.sha, c.ts, c.author, c.subject),
                    )

                conn.execute("DELETE FROM todo WHERE repo=?", (entry.name,))
                for tf_path in find_todo_files(entry.path):
                    tf = parse_todo_file(entry.path, tf_path)
                    conn.execute(
                        """
                        INSERT INTO todo(
                          repo, file, open_count, done_count,
                          total_lines, sample
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry.name,
                            tf.rel_path,
                            tf.open_count,
                            tf.done_count,
                            tf.total_lines,
                            tf.sample,
                        ),
                    )

            conn.execute(
                """
                INSERT INTO collect_run(
                  ts, repo_count, ok_count, new_commit_repos
                ) VALUES(?, ?, ?, ?)
                """,
                (
                    now,
                    len(repos),
                    ok_count,
                    ",".join(new_commit_repos),
                ),
            )
    finally:
        conn.close()

    return {
        "ts": now,
        "repo_count": len(repos),
        "ok_count": ok_count,
        "new_commit_repos": new_commit_repos,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--db", type=Path, default=default_db_path())
    p.add_argument("--discover", action="store_true")
    p.add_argument("--all-authors", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    summary = collect(
        config_path=args.config.expanduser(),
        db_path=args.db.expanduser(),
        discover=args.discover,
        all_authors=args.all_authors,
    )
    print(
        f"collected: repos={summary['repo_count']} "
        f"ok={summary['ok_count']} "
        f"new_commits_in={len(summary['new_commit_repos'])} "
        f"db={args.db}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
