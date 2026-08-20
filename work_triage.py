#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Decide which worklog items are units of work worth a Jira issue.

Tag rules already drop bookkeeping lines (chore/style/ci), but triviality is
not always visible in the tag: "add gemini argparser" and "CDH 모니터링 스택
신규 구축" are both #feat and both short. That judgement needs a model, so this
module asks one — and keeps anything it cannot judge, because silently dropping
real work is worse than creating one issue too many.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from worklog_parser import WorkItem

BATCH_SIZE = 40

LEVEL_TASK = "task"
LEVEL_SUBTASK = "subtask"
LEVEL_SKIP = "skip"
LEVELS = frozenset({LEVEL_TASK, LEVEL_SUBTASK, LEVEL_SKIP})

LlmCaller = Callable[[Sequence[WorkItem]], Mapping[str, str]]

PROMPT_HEADER = """아래 작업 목록의 각 항목을 Jira 이슈 계층 중 어디에 둘지 판단해라.

[subtask] — 기본값
- 대부분의 작업은 여기에 해당한다
- 하나의 구체적 변경: 컬럼 추가, 지표 교정, 필터 확대, 로직 이관 등
- 더 큰 작업 묶음의 일부로 읽히는 것

[task] — 예외적으로만
- 그 자체로 독립된 단위 업무이고 다른 작업의 일부로 묶기 어려운 것
- 파이프라인·마트·서비스 신규 구축처럼 범위가 크고 후속 작업이 여럿 따라오는 것
- 확신이 없으면 task로 올리지 말고 subtask로 둔다

[skip] — 이슈로 만들지 않음
- 설정값·포트·버전·의존성 변경
- 네이밍 정리, 문서/주석 수정, 로그 문구 변경
- 결과물이 아니라 개발 편의를 위한 잡무

[규칙]
- 요약이 짧다고 사소한 것이 아니다. 작업의 범위와 영향으로 판단해라
- 애매하면 subtask로 둔다. 실제 업무를 누락시키지 말 것
- 출력은 JSON 배열 하나만. 설명, 코드블록, 사족 금지

[출력 형식]
[{"key": "<작업 key>", "level": "task" 또는 "subtask" 또는 "skip"}]
"""


@dataclass
class TriageResult:
    """Items split by the level they belong at."""

    tasks: list[WorkItem] = field(default_factory=list)
    subtasks: list[WorkItem] = field(default_factory=list)
    minor: list[WorkItem] = field(default_factory=list)


def build_prompt(items: Sequence[WorkItem]) -> str:
    """Build the triage prompt for one batch of items."""
    lines = [PROMPT_HEADER, "[작업 목록]"]
    for item in items:
        tags = " ".join(f"#{tag}" for tag in item.tags)
        lines.append(f"- key={item.key()} project={item.project} {tags}".rstrip())
        lines.append(f"  {item.summary}")
    return "\n".join(lines)


def parse_response(text: str) -> dict[str, str]:
    """Extract the level map from noisy CLI output."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return {}

    try:
        decoded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, list):
        return {}

    verdicts: dict[str, str] = {}
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        level = entry.get("level")
        if isinstance(key, str) and isinstance(level, str) and level in LEVELS:
            verdicts[key] = level
    return verdicts


def triage(items: Sequence[WorkItem], llm: LlmCaller | None) -> TriageResult:
    """Split items into standalone tasks, sub-tasks, and ones too minor to track.

    Anything the model does not judge — because it abstained, returned junk, or
    failed outright — falls back to sub-task, the default level.
    """
    result = TriageResult()
    if not items:
        return result
    if llm is None:
        result.subtasks.extend(items)
        return result

    for start in range(0, len(items), BATCH_SIZE):
        batch = list(items[start : start + BATCH_SIZE])
        try:
            verdicts = llm(batch)
        except Exception:  # noqa: BLE001 - a failed batch must not drop real work
            result.subtasks.extend(batch)
            continue

        for item in batch:
            level = verdicts.get(item.key(), LEVEL_SUBTASK)
            if level == LEVEL_SKIP:
                result.minor.append(item)
            elif level == LEVEL_TASK:
                result.tasks.append(item)
            else:
                result.subtasks.append(item)
    return result
