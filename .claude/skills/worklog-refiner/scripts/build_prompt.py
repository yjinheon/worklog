#!/usr/bin/env -S uv run --python 3.12 --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Build the LLM prompt that refines a raw daily worklog into standardized Markdown.

Usage (standalone):
    build_prompt.py --input /tmp/worklog-2026-04-20.raw.md \\
                    --config ~/workspace/worklog/config.yaml

Usage (imported):
    from build_prompt import build_prompt
    prompt = build_prompt(input_path=..., config_path=...)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROMPT_HEADER = """다음은 오늘 여러 repository의 git commit 로그다.
아래 표준 양식의 일일 업무일지로 변환해라.

[양식 규칙]
- 첫 줄: ## YYYY-MM-DD
- 인용구로 메타데이터: > author: ..., > tags: #t1 #t2 ...
- ### Summary: 2~3줄 산문 요약 (한국어)
  요약시 개조식 명사형 종결로 bullet point사용해서 간결하게 작성
- ### Projects: repository 폴더명을 프로젝트명으로 사용해 #### 헤딩으로 작성
  - 각 작업: - [x] 한 줄 설명 + 인라인 태그(`#tag`)
  - #todo는 모든 작업에 넣고 다른 인라인 태그 추가가 필요할경우 아래 있는 Types 로한정할것
Types: feat, fix, refactor, perf, docs, test, chore, build, ci, style, revert

- 각 작업 대해 아래 양식적용

진행 중 작업
  - [ ] 작업내용 #project/프로젝트명 #todo [due:: 2026-04-20]

완료된 작업
  - [ ] 작업내용  #project/프로젝트명 #todo  [due:: 2026-02-05]  [completion:: 2026-02-09]

  - 진행 없는 카테고리는 _(진행 없음)_
- ### Repositories: 각 레포를 #### 헤딩, 커밋 short hash + subject 나열
- ### Notes: 회의/외근/메모 등 작업 외 사항 (없으면 섹션 생략)

[분류 규칙]
1. project 이름은 항상 repository path의 마지막 폴더명이다
2. `#project/<project-name>`의 `<project-name>`도 repository 폴더명과 동일하게 쓴다
3. project description은 작업 내용을 더 정확히 요약하기 위한 참고 정보로만 사용한다
4. project group은 비슷한 성격의 project를 나중에 묶어 보기 위한 메타데이터다
5. project group을 ### Projects의 heading이나 `#project/...` 태그로 사용하지 말 것

[출력]
- 순수 마크다운만
- 코드 블록(```)으로 전체를 감싸지 말 것
- 사족/설명/안내문 일절 금지
- 한국어로 작성

[예시]
- [ ] like, brand like 정보 원본 테이블 분리 #project/cdp-pipeline #todo #refactor [due:: 2026-04-03]
- [x] moltbook용 twitter 인증 기능 테스트 #project/moltbook #todo  [due:: 2026-02-05]  [completion:: 2026-02-09]
"""


def render_config(config_path: Path) -> str:
    """config.yaml → LLM이 읽기 쉬운 구조화된 프롬프트 섹션."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    lines: list[str] = []

    project_groups = data.get("project_groups") or {}
    if project_groups:
        lines.append("[project_groups]  (묶어 보기 위한 메타데이터, project 이름으로 쓰지 말 것)")
        for group, value in project_groups.items():
            if isinstance(value, dict):
                description = value.get("description", "")
                if description:
                    lines.append(f"- {group}: {description}")
                else:
                    lines.append(f"- {group}")
            else:
                lines.append(f"- {group}: {value}")
        lines.append("")

    repos = data.get("repos") or []
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

    task_tags = data.get("task_tags") or []
    if task_tags:
        lines.append("[task_tags] " + ", ".join(task_tags))

    system_tags = data.get("system_tags") or []
    if system_tags:
        lines.append("[system_tags] " + ", ".join(system_tags))

    return "\n".join(lines)


def build_prompt(input_path: Path, config_path: Path | None = None) -> str:
    """Assemble the full prompt string."""
    input_path = Path(input_path).expanduser()
    if not input_path.is_file():
        raise FileNotFoundError(f"input not found: {input_path}")

    parts: list[str] = [PROMPT_HEADER, ""]

    if config_path is not None:
        config_path = Path(config_path).expanduser()
        if config_path.is_file():
            parts.append("[프로젝트 매핑 사전]")
            parts.append(render_config(config_path))
            parts.append("")
        else:
            print(
                f"WARN: config not found, proceeding without: {config_path}",
                file=sys.stderr,
            )

    parts.append("[입력 데이터]")
    parts.append(input_path.read_text(encoding="utf-8"))

    return "\n".join(parts)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--input",
        required=True,
        type=Path,
        help="raw markdown produced by daily_worklog.py",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="optional config.yaml with project mappings",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        prompt = build_prompt(input_path=args.input, config_path=args.config)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(prompt)
    if not prompt.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
