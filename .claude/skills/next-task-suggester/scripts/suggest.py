#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///
"""suggest.py — entry point: collect (when stale) then print ranked recommendations.

Examples:
    suggest.py
    suggest.py --refresh --top 10
    suggest.py --mode balance
    suggest.py --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

# Allow running as a script: import sibling modules from this directory.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import collect as collect_mod  # noqa: E402
import score as score_mod  # noqa: E402

DEFAULT_CACHE_TTL = 3600


def latest_run_ts(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT MAX(ts) FROM collect_run"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def render_markdown(items, mode: str) -> str:
    today = date.today().isoformat()
    lines = [f"# Today's suggestions ({today})", f"_mode: {mode}_", ""]
    if not items:
        lines.append("_(no data — run with --refresh)_")
        return "\n".join(lines)
    for r in items:
        tag = f"  [{r.tag}]" if r.tag else ""
        lines.append(f"{r.rank}. {r.repo}{tag}")
        for reason in r.reasons:
            lines.append(f"   - {reason}")
        for sample in r.todo_samples:
            lines.append(f"     · {sample}")
        s = r.score
        lines.append(
            f"   - Score {s.total} "
            f"(R {s.R:.2f} · F {s.F:.2f} · T {s.T:.2f} · "
            f"I {s.I:.2f} · Rel {s.Rel:.2f})"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", type=Path, default=collect_mod.DEFAULT_CONFIG)
    p.add_argument("--db", type=Path, default=collect_mod.default_db_path())
    p.add_argument("--discover", action="store_true")
    p.add_argument("--all-authors", action="store_true")
    p.add_argument(
        "--mode", default="default", choices=sorted(score_mod.WEIGHT_PRESETS)
    )
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--refresh", action="store_true",
                   help="force collect even if cache is fresh")
    p.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL,
                   help="seconds; skip collect when last run is younger")
    p.add_argument("--json", dest="as_json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    db_path = args.db.expanduser()

    last = latest_run_ts(db_path)
    fresh = (
        last is not None
        and (int(time.time()) - last) < args.cache_ttl
        and not args.refresh
    )
    if not fresh:
        summary = collect_mod.collect(
            config_path=args.config.expanduser(),
            db_path=db_path,
            discover=args.discover,
            all_authors=args.all_authors,
        )
        print(
            f"# collected: repos={summary['repo_count']} "
            f"ok={summary['ok_count']} "
            f"new_commits_in={len(summary['new_commit_repos'])}",
            file=sys.stderr,
        )
    else:
        age = int(time.time()) - last
        print(
            f"# cache hit: last collect {age}s ago "
            f"(ttl {args.cache_ttl}s, use --refresh to override)",
            file=sys.stderr,
        )

    items = score_mod.rank_from_db(
        db_path=db_path, mode=args.mode, top=args.top
    )

    if args.as_json:
        json.dump(score_mod.to_jsonable(items), sys.stdout,
                  indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(render_markdown(items, mode=args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
