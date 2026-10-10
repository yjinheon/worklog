#!/usr/bin/env python3
"""Incremental raw worklog updater for DAILY_LOG/DAILY_LOG.md.

Reads ~/.claude-mem/claude-mem.db (+ WAL, via a normal read connection) and
records every observation / user_prompt whose created_at_epoch is newer than a
checkpoint stored next to this script.

Deterministic, no LLM. Runs once per cron tick; a quiet run (no new records)
prints a single line. Re-run is idempotent: each pass appends only records
newer than the last checkpoint.

The output lives in a fenced machine block pinned to the TOP of the log file:

    <!-- daily_log_update:start -->
    ## 업데이트 <range> KST  (N obs · M prompts)
    - ...
    <!-- daily_log_update:end -->

Each tick prepends a dated subsection (newest first) inside the block, so the
block is the live raw tail of the day's activity and is trivially relocatable
and dedup-able.

Usage:
  daily_log_update.py            # update the log (default path) + advance checkpoint
  daily_log_update.py --stdout   # print the new subsection to stdout, touch nothing
  daily_log_update.py --dry-run  # same as --stdout + report new/total counts
"""

import argparse
import datetime
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
DB = HOME / ".claude-mem" / "claude-mem.db"
LOG = HOME / "workspace" / "worklog" / "DAILY_LOG" / "DAILY_LOG.md"
CKPT = Path(__file__).resolve().with_name(".daily_log_update.ckpt")
TZ = datetime.timezone(datetime.timedelta(hours=9))
START = "<!-- daily_log_update:start -->"
END = "<!-- daily_log_update:end -->"
PROMPT_MAX = 160
TITLES = re.compile(r"conversation title|title generator|Output ONLY the title")


def kst(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def hhmm(ms):
    return kst(ms)[11:16]


def day(ms):
    return kst(ms)[:10]


def q(conn, sql, args=()):
    """Run a read query, retrying only on WAL busy/locked (the DB is live)."""
    last = None
    for _ in range(10):
        try:
            return conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError as e:
            if "busy" in str(e).lower() or "locked" in str(e).lower():
                last = e
                time.sleep(1.0)
                continue
            raise
    raise sqlite3.OperationalError(f"timed out on: {sql}") from last


def clean_prompt(p):
    p = (p or "").strip().replace("\n", " ")
    return p if not TITLES.search(p[:200]) else None


def build_section(lo, hi, obs, ups):
    L = [
        f"## 업데이트 {kst(lo)} → {kst(hi)} KST  ({len(obs)} obs · {len(ups)} prompts)",
        "",
    ]
    for r in obs:
        t = (r["title"] or "").strip()
        if not t:
            continue
        sub = (r["subtitle"] or "").strip()
        files = (r["files_modified"] or "").strip()
        L.append(
            f"- **[{day(r['created_at_epoch'])} {hhmm(r['created_at_epoch'])}] [{r['project']}/{r['type']}]** {t}"
        )
        if sub:
            L.append(f"  - {sub}")
        if files and files != "[]":
            L.append(f"  - files: {files}")
    if ups:
        L.append("")
        L.append("### 사용자 프롬프트")
        for p, pr, e in ups:
            L.append(
                f"- [{hhmm(e)}] **[{pr}]** {p[:PROMPT_MAX]}{'…' if len(p) > PROMPT_MAX else ''}"
            )
    L.append("")
    return "\n".join(L)


def remove_block(text):
    """Return (rest, inner_old). If no complete block, inner_old is None."""
    s = text.find(START)
    e = text.find(END)
    if s < 0 or e < 0 or e < s:
        return text, None
    inner = text[s + len(START) : e]
    rest = text[:s] + text[e + len(END) :]
    rest = re.sub(r"\n{3,}", "\n\n", rest)
    return rest, inner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--stdout", action="store_true", help="print new subsection, no file writes"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print new subsection + counts, no file writes",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="suppress all stdout (no-LLM watchdog: file update only)",
    )
    args = ap.parse_args()
    dry = args.dry_run or args.stdout

    def say(msg):
        if not args.quiet:
            print(msg)

    ck = 0
    if CKPT.exists():
        try:
            ck = int(CKPT.read_text().strip())
        except Exception:
            ck = 0

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    omax = q(con, "SELECT MAX(created_at_epoch) m FROM observations")[0]["m"] or 0
    pmax = q(con, "SELECT MAX(created_at_epoch) m FROM user_prompts")[0]["m"] or 0
    hi = max(omax, pmax)
    if hi <= ck:
        say(f"[daily_log_update] no new records (checkpoint {ck}, latest {hi})")
        return
    lo = max(ck, int(time.time() * 1000) - 86_400_000)  # safety net: ~1 day

    obs = q(
        con,
        """SELECT project,type,title,subtitle,files_modified,created_at_epoch
                    FROM observations WHERE created_at_epoch>? AND created_at_epoch<=?
                    ORDER BY created_at_epoch,id""",
        (lo, hi),
    )
    ups = q(
        con,
        """SELECT up.prompt_text,ss.project,up.created_at_epoch
                    FROM user_prompts up LEFT JOIN sdk_sessions ss ON up.session_db_id=ss.id
                    WHERE up.created_at_epoch>? AND up.created_at_epoch<=?
                    ORDER BY up.created_at_epoch,up.id""",
        (lo, hi),
    )
    ups = [
        (clean_prompt(u["prompt_text"]), u["project"], u["created_at_epoch"])
        for u in ups
    ]
    ups = [(p, pr, e) for (p, pr, e) in ups if p]

    n = len(obs) + len(ups)
    if n == 0:
        say(f"[daily_log_update] no usable records (checkpoint {ck}, latest {hi})")
        return

    subsection = build_section(lo, hi, obs, ups)

    if dry:
        print(subsection)
        print(
            f"[daily_log_update] DRY: {n} new (obs {len(obs)}, prompts {len(ups)}), "
            f"checkpoint {ck} -> {hi}",
            file=sys.stderr,
        )
        return

    text = LOG.read_text()
    rest, inner_old = remove_block(text)
    if inner_old and inner_old.strip():
        new_block = (
            START
            + "\n"
            + subsection.rstrip("\n")
            + "\n\n"
            + inner_old.strip("\n")
            + "\n"
            + END
        )
    else:
        new_block = START + "\n" + subsection.rstrip("\n") + "\n" + END
    new_text = new_block + "\n\n" + rest.lstrip("\n")
    LOG.write_text(new_text)
    CKPT.write_text(str(hi))
    say(
        f"[daily_log_update] +{n} (obs {len(obs)}, prompts {len(ups)}), checkpoint -> {hi}"
    )


if __name__ == "__main__":
    main()
