#!/usr/bin/env python3

"""run_worklog.py — Orchestrate daily worklog extraction + LLM refinement.

Usage:
    run_worklog.py [DATE]   # DATE=YYYY-MM-DD, defaults to today
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACT_SCRIPT = SCRIPT_DIR / "daily_worklog.py"
REFINE_SCRIPT = SCRIPT_DIR / "refine_worklog.py"


def extract_git_log(target_date: date, raw_path: Path) -> None:
    print(f"==> [1/2] extracting git log for {target_date}")
    env = os.environ.copy()
    env["OUTPUT"] = str(raw_path)
    # 각 스크립트는 자체 uv script shebang으로 의존성 해결
    result = subprocess.run([str(EXTRACT_SCRIPT), str(target_date)], env=env)
    if result.returncode != 0:
        sys.exit(f"ERROR: extraction failed (exit={result.returncode})")


def refine_with_llm(raw_path: Path, backend: str) -> int:
    print(f"==> [2/2] refining with LLM (backend={backend})")
    cmd = [str(REFINE_SCRIPT), str(raw_path), "--backend", backend]
    result = subprocess.run(cmd)
    return result.returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrate daily worklog extraction + LLM refinement."
    )
    parser.add_argument(
        "date", nargs="?", help="Target date (YYYY-MM-DD), defaults to today."
    )
    parser.add_argument(
        "--backend",
        "-b",
        choices=["claude", "gemini", "auto"],
        default="auto",
        help="LLM backend to use (default: auto)",
    )
    args = parser.parse_args(argv[1:])

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    raw_path = Path(f"/tmp/worklog-{target_date}.raw.md")

    extract_git_log(target_date, raw_path)

    if not raw_path.is_file() or raw_path.stat().st_size == 0:
        print(f"no commits found for {target_date}, exiting", file=sys.stderr)
        return 0

    rc = refine_with_llm(raw_path, args.backend)
    if rc != 0:
        return rc

    print("==> done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
