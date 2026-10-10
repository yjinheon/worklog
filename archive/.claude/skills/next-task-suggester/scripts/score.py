#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""score.py — read SQLite cache, compute weighted signals, return ranked list.

Pure-function module. Importable from suggest.py; also runnable for ad-hoc
debugging:

    score.py --db PATH --top 5
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

INTEREST_WINDOW = 5
RECENCY_HALF_LIFE_HOURS = 72.0
RECENCY_LAMBDA = math.log(2) / RECENCY_HALF_LIFE_HOURS  # so exp(-λ·72)=0.5
TODO_DENOM = 5.0

WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "default":  {"R": 0.35, "F": 0.20, "T": 0.25, "I": 0.10, "Rel": 0.10},
    "momentum": {"R": 0.50, "F": 0.30, "T": 0.10, "I": 0.05, "Rel": 0.05},
    "balance":  {"R": 0.15, "F": 0.10, "T": 0.40, "I": 0.20, "Rel": 0.15},
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "for", "in", "of", "with", "on",
    "fix", "feat", "chore", "docs", "test", "refactor", "ci", "build",
    "add", "update", "remove", "use", "make", "set", "get",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class RepoState:
    name: str
    path: str
    project: str | None
    branch: str | None
    commits: list[tuple[int, str]] = field(default_factory=list)  # (ts, subject)
    open_todo: int = 0
    todo_samples: list[str] = field(default_factory=list)
    todo_files: list[str] = field(default_factory=list)
    interest_runs: int = 0  # # of last-N runs containing this repo

    @property
    def last_commit_ts(self) -> int | None:
        return self.commits[0][0] if self.commits else None


@dataclass
class Score:
    R: float
    F: float
    T: float
    I: float  # noqa: E741 — domain term (Interest signal), preserved across docs/output
    Rel: float
    total: float
    weights: dict[str, float]


@dataclass
class Ranked:
    rank: int
    repo: str
    project: str | None
    tag: str | None
    score: Score
    reasons: list[str]
    last_commit_ts: int | None
    open_todo: int
    todo_samples: list[str]


def recency(now: int, last_ts: int | None) -> float:
    if last_ts is None:
        return 0.0
    hours = max(0.0, (now - last_ts) / 3600.0)
    return math.exp(-RECENCY_LAMBDA * hours)


def frequency(commits: list[tuple[int, str]], now: int) -> float:
    """Mean inter-commit span over the last 30 days, normalized so 1/day → 1.0."""
    cutoff = now - 30 * 24 * 3600
    timestamps = [ts for ts, _ in commits if ts >= cutoff]
    if len(timestamps) < 2:
        return 0.0
    timestamps.sort(reverse=True)
    spans = [timestamps[i] - timestamps[i + 1] for i in range(len(timestamps) - 1)]
    avg = sum(spans) / len(spans)
    if avg <= 0:
        return 1.0
    return min(1.0, 86400.0 / avg)


def todo_signal(open_count: int) -> float:
    if open_count <= 0:
        return 0.0
    return min(1.0, open_count / TODO_DENOM)


def interest(state: RepoState, run_window: int) -> float:
    denom = max(1, run_window)
    return min(1.0, state.interest_runs / denom)


def tokenize(text: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}


def relevance(state: RepoState, ref: RepoState | None) -> float:
    if ref is None or ref.name == state.name:
        return 0.0
    a_text = state.name + " " + " ".join(s for _, s in state.commits)
    b_text = ref.name + " " + " ".join(s for _, s in ref.commits)
    a, b = tokenize(a_text), tokenize(b_text)
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    if not union:
        return 0.0
    return len(inter) / len(union)


def assign_tag(state: RepoState, score_R: float, score_Rel: float, now: int) -> str | None:
    last = state.last_commit_ts
    age_h = (now - last) / 3600 if last else None
    if age_h is not None and age_h <= 24:
        return "continue"
    if state.open_todo >= 3 and (age_h is None or age_h >= 72):
        return "resume"
    if score_Rel >= 0.20 and score_R < 0.20:
        return "explore"
    return None


def humanize_age(seconds: float) -> str:
    h = seconds / 3600
    if h < 1:
        return f"{int(seconds // 60)}m ago"
    if h < 24:
        return f"{h:.0f}h ago"
    return f"{h / 24:.0f}d ago"


def load_states(conn: sqlite3.Connection) -> dict[str, RepoState]:
    states: dict[str, RepoState] = {}
    for row in conn.execute(
        "SELECT name, path, project, branch FROM repo"
    ):
        states[row[0]] = RepoState(
            name=row[0], path=row[1], project=row[2], branch=row[3]
        )
    for row in conn.execute(
        "SELECT repo, ts, subject FROM commit_log ORDER BY repo, ts DESC"
    ):
        if row[0] in states:
            states[row[0]].commits.append((int(row[1]), row[2]))
    for row in conn.execute(
        "SELECT repo, file, open_count, sample FROM todo"
    ):
        st = states.get(row[0])
        if not st:
            continue
        st.open_todo += int(row[2])
        st.todo_files.append(row[1])
        if row[3]:
            for line in row[3].splitlines():
                if line and len(st.todo_samples) < 3:
                    st.todo_samples.append(line)

    # interest = # of last N collect_run rows whose new_commit_repos contains this repo
    runs = list(
        conn.execute(
            "SELECT new_commit_repos FROM collect_run "
            "ORDER BY ts DESC LIMIT ?",
            (INTEREST_WINDOW,),
        )
    )
    for (csv,) in runs:
        if not csv:
            continue
        for name in csv.split(","):
            name = name.strip()
            if name in states:
                states[name].interest_runs += 1
    return states


def rank(
    states: dict[str, RepoState],
    weights: dict[str, float],
    now: int,
    top: int | None,
) -> list[Ranked]:
    if not states:
        return []
    scored_R = {n: recency(now, s.last_commit_ts) for n, s in states.items()}
    # reference repo for Rel = highest-R among states
    ref_name = max(scored_R, key=lambda n: scored_R[n])
    ref = states[ref_name]

    items: list[Ranked] = []
    interest_window = max(
        1, min(INTEREST_WINDOW, max(s.interest_runs for s in states.values()) or 1)
    )

    for name, st in states.items():
        R = scored_R[name]
        F = frequency(st.commits, now)
        T = todo_signal(st.open_todo)
        I = interest(st, interest_window)  # noqa: E741 — Interest signal (domain term)
        Rel = relevance(st, ref if ref.name != name else None)
        total = (
            weights["R"] * R
            + weights["F"] * F
            + weights["T"] * T
            + weights["I"] * I
            + weights["Rel"] * Rel
        )
        score = Score(R=R, F=F, T=T, I=I, Rel=Rel, total=round(total, 3),
                      weights=weights)
        tag = assign_tag(st, R, Rel, now)
        reasons: list[str] = []
        if st.last_commit_ts is not None:
            age_s = now - st.last_commit_ts
            reasons.append(f"last commit {humanize_age(age_s)}")
        else:
            reasons.append("no own-author commits in cache")
        if len(st.commits) >= 2:
            spans = [
                st.commits[i][0] - st.commits[i + 1][0]
                for i in range(len(st.commits) - 1)
            ]
            avg_h = (sum(spans) / len(spans)) / 3600 if spans else 0
            reasons.append(
                f"{len(st.commits)} commits in last-5 (avg ~{avg_h:.0f}h)"
            )
        if st.open_todo:
            files_str = ", ".join(st.todo_files[:2])
            reasons.append(f"{files_str}: {st.open_todo} open")
        if Rel >= 0.20 and ref.name != name:
            reasons.append(
                f"tokens overlap with active '{ref.name}' (Rel {Rel:.2f})"
            )
        items.append(
            Ranked(
                rank=0,
                repo=name,
                project=st.project,
                tag=tag,
                score=score,
                reasons=reasons,
                last_commit_ts=st.last_commit_ts,
                open_todo=st.open_todo,
                todo_samples=list(st.todo_samples),
            )
        )

    items.sort(key=lambda r: r.score.total, reverse=True)
    if top is not None:
        items = items[:top]
    for i, r in enumerate(items, start=1):
        r.rank = i
    return items


def rank_from_db(
    db_path: Path,
    mode: str = "default",
    top: int | None = None,
    now: int | None = None,
) -> list[Ranked]:
    if mode not in WEIGHT_PRESETS:
        raise ValueError(
            f"unknown mode '{mode}'. options: {sorted(WEIGHT_PRESETS)}"
        )
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        states = load_states(conn)
    finally:
        conn.close()
    return rank(
        states=states,
        weights=WEIGHT_PRESETS[mode],
        now=now if now is not None else int(time.time()),
        top=top,
    )


def to_jsonable(items: list[Ranked]) -> list[dict]:
    out: list[dict] = []
    for r in items:
        d = asdict(r)
        d["score"] = {
            "R": round(r.score.R, 3),
            "F": round(r.score.F, 3),
            "T": round(r.score.T, 3),
            "I": round(r.score.I, 3),
            "Rel": round(r.score.Rel, 3),
            "total": r.score.total,
        }
        out.append(d)
    return out


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--mode", default="default", choices=sorted(WEIGHT_PRESETS))
    p.add_argument("--top", type=int, default=None)
    p.add_argument("--json", dest="as_json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    items = rank_from_db(db_path=args.db.expanduser(), mode=args.mode, top=args.top)
    if args.as_json:
        json.dump(to_jsonable(items), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    for r in items:
        tag = f"  [{r.tag}]" if r.tag else ""
        print(f"{r.rank}. {r.repo}{tag}  score={r.score.total}")
        for reason in r.reasons:
            print(f"   - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
