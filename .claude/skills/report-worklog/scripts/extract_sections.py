#!/usr/bin/env python3
"""Extract every DAILY_LOG section for one date and emit them as a single digest.

DAILY_LOG.md has two shapes, both supported:
- legacy: one `## <date>` heading per work session ("(세션 2)", ...), newest-first.
- cron (scripts/daily_log_update.py): `## 업데이트 <from> → <to> KST` blocks holding
  `- **[YYYY-MM-DD HH:MM] [repo/type]** title` observations and `- [HH:MM] **[repo]**`
  prompts. A block can span midnight, so entries are filtered by their own date.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as date_cls
from pathlib import Path

DATE_HEADING = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b(.*)$")
SESSION_NO = re.compile(r"\(\s*세션\s*(\d+)\s*\)")
RESUME_HEADING = re.compile(r"^###\s+Resume\s*/\s*Portfolio\b")
ANY_H3 = re.compile(r"^###\s+")
UPDATE_HEADING = re.compile(
    r"^##\s+업데이트\s+(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\S*\s+→\s+(\d{4}-\d{2}-\d{2})"
)
OBS_ENTRY = re.compile(r"^- \*\*\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\] \[([^\]/]+)")
PROMPT_ENTRY = re.compile(r"^- \[(\d{2}:\d{2})\] \*\*\[([^\]]+)\]\*\*")


def update_entries(lines: list[str], target: str) -> tuple[list[tuple], list[tuple]]:
    """Return (observations, prompts) from cron update blocks dated `target`.

    Each item is (sort_key, repo, text_lines). Prompt dates come from the block
    range: time >= block start time -> start date, else end date.
    """
    obs: list[tuple] = []
    prompts: list[tuple] = []
    block: tuple[str, str, str] | None = None
    current: list[str] | None = None
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("  ") and current is not None:
            current.append(line)
            continue
        current = None
        if line.startswith("## "):
            match = UPDATE_HEADING.match(line)
            block = match.groups() if match else None
            continue
        if block is None:
            continue
        if match := OBS_ENTRY.match(line):
            day, hhmm, repo = match.groups()
            if day == target:
                current = [line]
                obs.append((f"{day} {hhmm}", repo, current))
        elif match := PROMPT_ENTRY.match(line):
            hhmm, repo = match.groups()
            start_day, start_time, end_day = block
            day = start_day if hhmm >= start_time else end_day
            if day == target:
                prompts.append((f"{day} {hhmm}", repo, [line]))
    obs.sort(key=lambda item: item[0])
    prompts.sort(key=lambda item: item[0])
    return obs, prompts


def render_updates(obs: list[tuple], prompts: list[tuple]) -> list[str]:
    counts: dict[str, int] = {}
    for _, repo, _ in obs:
        counts[repo] = counts.get(repo, 0) + 1
    by_repo = " · ".join(f"{r} {n}" for r, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    chunks = [f"## 관찰 ({len(obs)}) — {by_repo}", ""]
    for _, _, text in obs:
        chunks.extend(text)
    chunks += ["", f"## 사용자 프롬프트 ({len(prompts)})", ""]
    for _, _, text in prompts:
        chunks.extend(text)
    return chunks


def split_sections(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """Return [(date, heading, body_lines)] for every `## <date>` section."""
    sections: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in lines:
        match = DATE_HEADING.match(line)
        if match:
            if current is not None:
                sections.append(current)
            current = (match.group(1), line.rstrip("\n"), [])
        elif current is not None:
            current[2].append(line.rstrip("\n"))
    if current is not None:
        sections.append(current)
    return sections


def session_order(heading: str) -> int:
    """Session 1 has no suffix; later sessions carry `(세션 N)`."""
    match = SESSION_NO.search(heading)
    return int(match.group(1)) if match else 1


def drop_resume(body: list[str]) -> list[str]:
    """Remove the `### Resume / Portfolio 반영 초안` block — it is a separate output."""
    kept: list[str] = []
    skipping = False
    for line in body:
        if RESUME_HEADING.match(line):
            skipping = True
            continue
        if skipping:
            if ANY_H3.match(line) or DATE_HEADING.match(line):
                skipping = False
            else:
                continue
        kept.append(line)
    return kept


def strip_edges(body: list[str]) -> list[str]:
    """Trim blank lines and the trailing `---` separator."""
    while body and not body[-1].strip():
        body.pop()
    while body and body[-1].strip() == "---":
        body.pop()
        while body and not body[-1].strip():
            body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return body


def build_digest(path: Path, target: str, keep_resume: bool) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    matched = [s for s in split_sections(lines) if s[0] == target]
    obs, prompts = update_entries(lines, target)
    if not matched and not obs and not prompts:
        raise SystemExit(f"no DAILY_LOG section found for {target} in {path}")
    matched.sort(key=lambda s: session_order(s[1]))

    plural = "" if len(matched) == 1 else "s"
    chunks: list[str] = [f"# DAILY_LOG digest {target} ({len(matched)} session{plural})", ""]
    if obs or prompts:
        chunks.extend(render_updates(obs, prompts))
        chunks.append("")
    for heading, body in ((s[1], s[2]) for s in matched):
        body = body if keep_resume else drop_resume(body)
        chunks.append(heading)
        chunks.append("")
        chunks.extend(strip_edges(body))
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", default=date_cls.today().isoformat(),
                        help="target date, YYYY-MM-DD (default: today)")
    parser.add_argument("--log", default="DAILY_LOG/DAILY_LOG.md",
                        help="path to DAILY_LOG.md (default: DAILY_LOG/DAILY_LOG.md)")
    parser.add_argument("--keep-resume", action="store_true",
                        help="keep the `Resume / Portfolio 반영 초안` blocks")
    parser.add_argument("--list", action="store_true",
                        help="print matching section headings only")
    args = parser.parse_args()

    path = Path(args.log)
    if not path.is_file():
        raise SystemExit(f"DAILY_LOG not found: {path}")

    if args.list:
        lines = path.read_text(encoding="utf-8").splitlines()
        found = [s[1] for s in split_sections(lines) if s[0] == args.date]
        found += [l for l in lines if (m := UPDATE_HEADING.match(l)) and args.date in (m[1], m[3])]
        if not found:
            raise SystemExit(f"no DAILY_LOG section found for {args.date} in {path}")
        found.sort(key=session_order)
        print("\n".join(found))
        return

    sys.stdout.write(build_digest(path, args.date, args.keep_resume))


if __name__ == "__main__":
    main()
