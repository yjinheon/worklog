#!/usr/bin/env -S uv run --python 3.13 --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///
"""hh_worklog.py — Answer hh_spec.md from raw daily worklog markdown via LLM.

Usage:
    ./hh_worklog.py /tmp/worklog-2026-05-07.raw.md
    ./hh_worklog.py /tmp/worklog-2026-05-07.raw.md --backend gemini
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from refine_worklog import call_claude, call_codex, call_gemini, call_kimi

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SPEC_PATH = SCRIPT_DIR / "hh_spec.md"
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DAILY_SCRIPT = SCRIPT_DIR / "daily_worklog.py"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_JIRA_BASE_URL = "https://humuson.atlassian.net"


def render_config(config: dict) -> str:
    lines: list[str] = []

    project_groups = config.get("project_groups") or {}
    if project_groups:
        lines.append("[project_groups]  (묶어 보기 위한 메타데이터, project 이름으로 쓰지 말 것)")
        for group, value in project_groups.items():
            if isinstance(value, dict):
                description = value.get("description", "")
                lines.append(f"- {group}: {description}" if description else f"- {group}")
            else:
                lines.append(f"- {group}: {value}")
        lines.append("")

    repos = config.get("repos") or []
    if repos:
        lines.append("[projects]  (project 이름은 path의 마지막 폴더명)")
        for entry in repos:
            if isinstance(entry, str):
                path = entry
                description = ""
                group = ""
            elif isinstance(entry, dict):
                path = entry.get("path", "")
                description = entry.get("description", "")
                group = entry.get("group", "")
            else:
                continue

            name = Path(path).name
            if not name:
                continue

            attrs: list[str] = []
            if description:
                attrs.append(f"description={description}")
            if group:
                attrs.append(f"group={group}")
            suffix = f" ({'; '.join(attrs)})" if attrs else ""
            lines.append(f"- {name}{suffix}")
        lines.append("")

    task_tags = config.get("task_tags") or []
    if task_tags:
        lines.append("[task_tags] " + ", ".join(task_tags))

    system_tags = config.get("system_tags") or []
    if system_tags:
        lines.append("[system_tags] " + ", ".join(system_tags))

    return "\n".join(lines)


def load_config(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        sys.exit(f"ERROR: cannot parse config: {path}: {exc}")
    if not isinstance(data, dict):
        sys.exit(f"ERROR: config root must be a mapping: {path}")
    return data


def build_prompt(
    raw_markdown: str,
    spec_markdown: str,
    config: dict | None = None,
    *,
    output_date: str | None = None,
) -> str:
    config_section = ""
    if config:
        rendered_config = render_config(config).strip()
        if rendered_config:
            config_section = f"""
[프로젝트/태그 설정]
아래 설정은 답변에서 프로젝트명, 프로젝트 맥락, 태그 후보를 판단하기 위한 참고 정보입니다.
project group은 분류 메타데이터이며 project 이름으로 쓰지 마세요.

{rendered_config}
"""

    date_line = output_date or "YYYY-MM-DD"
    base_url = DEFAULT_JIRA_BASE_URL
    if config:
        configured = config.get("JIRA_BASE_URL") or config.get("CONFLUENCE_BASE_URL")
        if isinstance(configured, str) and configured.strip():
            base_url = configured.strip().rstrip("/")
    issue_link = f"[DC-123]({base_url}/browse/DC-123)"

    return f"""아래 raw worklog markdown만 근거로 일일 업무 요약을 작성하세요.

출력 규칙:
- 최종 출력은 반드시 아래 형식만 사용
- 첫 줄은 날짜만 작성: {date_line}
- 날짜 다음에는 빈 줄 하나를 둠
- 이후 [이슈사항], [검토사항], [작업완료 사항] 세 섹션을 순서대로 작성
- 각 섹션은 "이슈사항", "검토사항", "작업완료 사항" 제목 한 줄로 시작하고 다음 줄부터 항목을 작성
- 제목과 항목 사이, 항목과 항목 사이, 섹션과 섹션 사이에는 빈 줄 하나를 둠
- 근거가 없는 섹션은 제목 아래에 "- 없음" 한 줄만 작성

[이슈사항]
- 오늘 발생한 이슈나 작업의 배경을 "- " bullet로 작성
- 각 항목은 간결한 한국어 명사형으로 작성

[검토사항]
- 진행 중 막힌 부분이나 선택의 기로에서 고민한 점, 추가 검토가 필요한 사항을 "- " bullet로 작성

[작업완료 사항]
- 실제로 수행/완료한 작업을 "- " bullet로 한 줄씩 작성
- 업무 요약은 Jira 이슈 제목이 되므로 **40자 이내**의 짧은 개조식 명사형으로 종결
  - 대상(모듈/테이블/기능)과 한 일이 드러나면 충분하다
  - 세부 변경 사항을 쉼표로 길게 나열하지 말 것. 목적이 다르면 줄을 나눈다
  - 예) "campaign_active 플래그 추가", "전환 윈도우 trk_period 적용"
- "코드 수정", "작업 진행" 같은 모호한 표현 대신 대상 모듈/기능/파일 영역을 명시
  (commit subject뿐 아니라 그 아래 들여쓴 commit body와 files changed 규모를 근거로 활용)
- 작업 형식:
  - [project] [tag, tag] 업무 요약 #project/프로젝트명 #tag {issue_link}
- project는 config의 repo 마지막 폴더명 또는 raw worklog의 프로젝트명을 사용
- #project/프로젝트명 의 프로젝트명도 project와 동일하게 사용
- tag와 #tag는 feat, fix, docs, test, refactor 같은 task tag와 dbt, clickhouse 같은 system tag를 필요할 때 함께 사용
- 줄 끝의 {issue_link} 는 그 작업이 어떤 Jira 이슈에 대한 것인지 나타내는 링크다
  - raw worklog나 commit 메시지/브랜치명에 이슈 key(DC-숫자)가 실제로 드러날 때만 적는다
  - 형식은 markdown 링크로 [DC-숫자]({base_url}/browse/DC-숫자) 를 사용한다
  - 근거가 없으면 생략한다. 이슈 key를 추측하거나 지어내지 말 것
  - 생략된 줄은 나중에 jira_create.py가 이슈를 생성한 뒤 링크로 자동 채운다
- 여러 커밋/작업이 같은 목적이면 한 줄로 묶고, 목적이 다르면 같은 project라도 여러 줄로 분리
- 회의나 커뮤니케이션 업무를 적을 수 있도록 아래 placeholder를 기본으로 포함
  - [회의] [meeting] 회의명/논의 내용/후속 조치 작성 필요 #project/회의 #meeting
- 과장, 추측, 원문에 없는 성과 추가 금지
- 프로젝트명과 태그 후보는 config 설정을 참고하되 raw worklog 근거가 있을 때만 사용
- markdown 제목(#), 코드블록, 설명 문장, hh_spec 질문 제목은 출력하지 않음

출력 예시:
{date_line}

이슈사항

- datalab-demo 대시보드 시각화 기능 확충 필요

검토사항

- gold repository 발행 방식(기존 방식 대 partition 교체 방식) 검토

작업완료 사항

- [project] [tag, tag] 짧은 업무 요약 #project/프로젝트명 #tag

- [project] [tag] 기존 이슈가 확인된 작업 요약 #project/프로젝트명 #tag {issue_link}

- [회의] [meeting] 회의명/논의 내용/후속 조치 작성 필요 #project/회의 #meeting

hh_spec 참고 질문:
{spec_markdown.strip()}
{config_section}

raw worklog markdown:
{raw_markdown.strip()}
"""


def trim_to_date_line(text: str, output_date: str | None) -> str:
    """Drop any preamble the CLI or model emitted before the date line.

    Hook banners and model side notes land ahead of the answer and would
    otherwise be written into the worklog file.
    """
    if not output_date:
        return text

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == output_date:
            return "".join(lines[index:])
    return text


def run_llm(
    prompt: str,
    *,
    backend: str,
    work_dir: Path,
    timeout: int,
) -> tuple[str | None, str]:
    backends_to_try = (
        [backend] if backend != "auto" else ["claude", "gemini", "kimi", "codex"]
    )

    for name in backends_to_try:
        if name == "claude":
            result = call_claude(prompt, work_dir=work_dir, timeout=timeout)
        elif name == "gemini":
            result = call_gemini(prompt, work_dir=work_dir, timeout=timeout)
        elif name == "kimi":
            result = call_kimi(prompt, work_dir=work_dir, timeout=timeout)
        else:
            result = call_codex(prompt, work_dir=work_dir, timeout=timeout)

        if result is not None:
            return result, name

    return None, ""


def resolve_input_path(value: str) -> Path:
    if DATE_PATTERN.fullmatch(value):
        return Path(f"/tmp/worklog-{value}.raw.md")
    return Path(value).expanduser().resolve()


def ensure_input_path(value: str) -> Path:
    input_path = resolve_input_path(value)

    # 명시적 파일 경로면 그대로 사용, 없으면 에러.
    if not DATE_PATTERN.fullmatch(value):
        if input_path.is_file():
            return input_path
        sys.exit(f"ERROR: input not found: {input_path}")

    # 날짜 입력이면 항상 새로 생성(오늘 커밋이 계속 늘어나므로 캐시 재사용 금지).
    result = subprocess.run([str(DAILY_SCRIPT), value])
    if result.returncode != 0:
        sys.exit(f"ERROR: raw worklog generation failed (exit={result.returncode})")
    if not input_path.is_file():
        sys.exit(f"ERROR: generated raw worklog not found: {input_path}")
    if input_path.stat().st_size == 0:
        sys.exit(f"ERROR: generated raw worklog is empty: {input_path}")
    return input_path


def resolve_output_date(input_value: str, input_path: Path) -> str | None:
    if DATE_PATTERN.fullmatch(input_value):
        return input_value
    match = re.search(r"\d{4}-\d{2}-\d{2}", input_path.name)
    if match:
        return match.group(0)
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Answer hh_spec.md from raw daily worklog markdown via LLM."
    )
    parser.add_argument(
        "input",
        help="Raw markdown path or date (YYYY-MM-DD) from daily_worklog.py.",
    )
    parser.add_argument(
        "--spec",
        default=str(DEFAULT_SPEC_PATH),
        help="Spec markdown path. Default: ./hh_spec.md",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG", str(DEFAULT_CONFIG_PATH)),
        help="Config YAML path. Default: ./config.yaml or CONFIG env",
    )
    parser.add_argument(
        "--backend",
        "-b",
        choices=["claude", "gemini", "kimi", "codex", "auto"],
        default="auto",
        help="LLM backend to use. Default: auto",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output markdown path. Default: <date>.md in cwd. Use '-' for stdout.",
    )
    args = parser.parse_args(argv[1:])

    input_path = ensure_input_path(args.input)
    spec_path = Path(args.spec).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()

    if not spec_path.is_file():
        sys.exit(f"ERROR: spec not found: {spec_path}")
    config = None
    if config_path.is_file():
        config = load_config(config_path)
    else:
        print(f"WARN: config not found, proceeding without: {config_path}", file=sys.stderr)

    timeout = int(os.environ.get("TIMEOUT_SEC", "180"))
    work_dir = Path(os.environ.get("WORK_DIR", "/tmp/worklog-cwd")).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)

    output_date = resolve_output_date(args.input, input_path)
    prompt = build_prompt(
        input_path.read_text(encoding="utf-8"),
        spec_path.read_text(encoding="utf-8"),
        config,
        output_date=output_date,
    )
    result, used = run_llm(
        prompt,
        backend=args.backend,
        work_dir=work_dir,
        timeout=timeout,
    )

    if result is None:
        print("ERROR: requested LLM backend(s) failed.", file=sys.stderr)
        return 2

    result = trim_to_date_line(result, output_date)
    text = result + ("\n" if not result.endswith("\n") else "")

    if args.output == "-":
        print(f"answered: {input_path} (engine={used})", file=sys.stderr)
        sys.stdout.write(text)
        return 0

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        stem = output_date or input_path.stem
        output_path = Path.cwd() / f"{stem}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"answered: {input_path} (engine={used})", file=sys.stderr)
    print(f"wrote: {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
