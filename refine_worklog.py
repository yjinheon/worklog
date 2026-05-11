#!/usr/bin/env -S uv run --python 3.12 --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""refine_worklog.py — Refine raw worklog markdown via Claude (subscription) with Gemini fallback.

Usage:
    ./refine_worklog.py <raw-md-path>
    OUTPUT=/path/to/out.md ./refine_worklog.py /tmp/worklog-2026-04-20.raw.md

Env:
    OUTPUT       Destination markdown path. Default: ~/workspace/worklog/<date>.md
    CONFIG       Config YAML path. Default: ~/workspace/worklog/config.yaml
    TIMEOUT_SEC  LLM call timeout (seconds). Default: 180
    WORK_DIR     Working dir for LLM subprocesses. Default: /tmp/worklog-cwd
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BUILD_PROMPT = (
    SCRIPT_DIR / ".claude" / "skills" / "worklog-refiner" / "scripts" / "build_prompt.py"
)

# Env keys to strip so the CLIs are forced to use subscription/OAuth, not API keys.
API_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def _subscription_env() -> dict[str, str]:
    env = os.environ.copy()
    # Ensure simple mode to avoid hook pollution
    env["CLAUDE_CODE_SIMPLE"] = "1"
    # Gemini CLI blocks headless runs in untrusted cwd unless this is explicit.
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    for key in API_KEY_ENV_VARS:
        env.pop(key, None)
    return env


def _clean_output(text: str) -> str:
    """Strip hook system noise from stdout."""
    lines = text.splitlines()
    start_idx = 0
    end_idx = len(lines)

    HOOK_PREFIXES = (
        "# [worklog]",
        "Legend:",
        "Format:",
        "Fetch details:",
        "Search:",
        "Stats:",
        "Access ",
        "View Observations ",
        "Hook system message:",
    )

    # Find hook footer
    for i, line in enumerate(lines):
        if "Created execution plan for SessionEnd" in line:
            end_idx = i
            break

    # Skip hook header lines
    for i in range(end_idx):
        line = lines[i].strip()
        if not line:
            continue
        if any(line.startswith(p) for p in HOOK_PREFIXES):
            continue
        start_idx = i
        break

    return "\n".join(lines[start_idx:end_idx]).strip()


def _derive_date(input_path: Path) -> str:
    """worklog-2026-04-20.raw.md → 2026-04-20"""
    stem = input_path.name
    if stem.endswith(".raw.md"):
        stem = stem[: -len(".raw.md")]
    elif stem.endswith(".md"):
        stem = stem[: -len(".md")]
    return stem.removeprefix("worklog-")


def build_prompt_text(input_path: Path, config_path: Path | None) -> str:
    """Invoke the skill's build_prompt.py and return its stdout."""
    if not BUILD_PROMPT.is_file():
        sys.exit(f"ERROR: prompt builder not found: {BUILD_PROMPT}")

    # build_prompt.py는 자체 uv script shebang으로 실행 (pyyaml 의존성 자가 해결)
    cmd = [str(BUILD_PROMPT), "--input", str(input_path)]
    if config_path is not None:
        cmd += ["--config", str(config_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(f"ERROR: build_prompt failed (exit={result.returncode})")
    return result.stdout


def call_claude(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
    if shutil.which("claude") is None:
        print("  claude not installed", file=sys.stderr)
        return None

    err_log = Path("/tmp/refine-claude.err")
    try:
        result = subprocess.run(
            [
                "claude",
                "-p", prompt,
                "--output-format", "text",
                "--tools", "",
            ],
            cwd=work_dir,
            env=_subscription_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        err_log.write_text("claude timed out\n")
        print(f"  claude failed (timeout; see {err_log})", file=sys.stderr)
        return None

    if result.returncode != 0:
        err_log.write_text(result.stderr)
        print(f"  claude failed (see {err_log})", file=sys.stderr)
        return None

    out = _clean_output(result.stdout)
    if not out:
        print("  claude returned empty output", file=sys.stderr)
        return None
    return out


def call_gemini(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
    if shutil.which("gemini") is None:
        print("  gemini not installed", file=sys.stderr)
        return None

    err_log = Path("/tmp/refine-gemini.err")
    try:
        result = subprocess.run(
            ["gemini", "-p", "", "--output-format", "text"],
            cwd=work_dir,
            env=_subscription_env(),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        err_log.write_text("gemini timed out\n")
        print(f"  gemini failed (timeout; see {err_log})", file=sys.stderr)
        return None

    if result.returncode != 0:
        err_log.write_text(result.stderr)
        print(f"  gemini failed (see {err_log})", file=sys.stderr)
        return None

    out = _clean_output(result.stdout)
    if not out:
        print("  gemini returned empty output", file=sys.stderr)
        return None
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Refine raw worklog markdown via LLM."
    )
    parser.add_argument("input_path", help="Path to the raw markdown file.")
    parser.add_argument(
        "--backend", "-b",
        choices=["claude", "gemini", "auto"],
        default="auto",
        help="LLM backend to use (default: auto)"
    )
    args = parser.parse_args(argv[1:])

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.is_file():
        sys.exit(f"ERROR: input not found: {input_path}")

    date = _derive_date(input_path)
    home = Path.home()
    output_path = Path(
        os.environ.get("OUTPUT", str(home / f"workspace/worklog/{date}.md"))
    ).expanduser()

    config_raw = os.environ.get("CONFIG", str(home / "workspace/worklog/config.yaml"))
    config_path: Path | None = Path(config_raw).expanduser()
    if not config_path.is_file():
        print(f"WARN: config not found, proceeding without: {config_path}",
              file=sys.stderr)
        config_path = None

    timeout = int(os.environ.get("TIMEOUT_SEC", "180"))
    work_dir = Path(os.environ.get("WORK_DIR", "/tmp/worklog-cwd")).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"refining: {input_path} (backend={args.backend})", file=sys.stderr)
    prompt = build_prompt_text(input_path, config_path)

    result: str | None = None
    used: str = ""

    # Strategy selection
    backends_to_try = []
    if args.backend == "claude":
        backends_to_try = ["claude"]
    elif args.backend == "gemini":
        backends_to_try = ["gemini"]
    else:  # auto
        backends_to_try = ["claude", "gemini"]

    for backend in backends_to_try:
        if backend == "claude":
            result = call_claude(prompt, work_dir=work_dir, timeout=timeout)
        elif backend == "gemini":
            result = call_gemini(prompt, work_dir=work_dir, timeout=timeout)

        if result is not None:
            used = backend
            break

    if result is None:
        print("ERROR: requested LLM backend(s) failed. Saving raw input as fallback.",
              file=sys.stderr)
        output_path.write_bytes(input_path.read_bytes())
        return 2

    output_path.write_text(result + ("\n" if not result.endswith("\n") else ""),
                           encoding="utf-8")
    print(f"wrote: {output_path} (engine={used})")
    return 0



if __name__ == "__main__":
    sys.exit(main(sys.argv))
