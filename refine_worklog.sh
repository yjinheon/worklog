#!/usr/bin/env bash

# refine-worklog.sh — raw 마크다운을 LLM으로 정제. Claude(구독) → Gemini(구독) fallback
set -euo pipefail

# ===== 인자/설정 =====
INPUT="${1:?usage: refine_worklog.sh <raw-md-path>}"
DATE=$(basename "$INPUT" .raw.md | sed 's/worklog-//')
OUTPUT="${OUTPUT:-$HOME/workspace/worklog/${DATE}.md}"
VOCAB="${VOCAB:-$HOME/workspace/worklog/vocab.yaml}"
TIMEOUT_SEC="${TIMEOUT_SEC:-180}"
WORK_DIR="${WORK_DIR:-/tmp/worklog-cwd}"

# ===== 핵심: API 키 환경변수 제거 (구독 OAuth 강제) =====
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS

# ===== 사전 점검 =====
[[ -f "$INPUT" ]] || { echo "ERROR: input not found: $INPUT" >&2; exit 1; }
mkdir -p "$WORK_DIR" "$(dirname "$OUTPUT")"

if [[ ! -f "$VOCAB" ]]; then
  echo "WARN: vocab not found, proceeding without: $VOCAB" >&2
  VOCAB=""
fi

# ===== 프롬프트 조립 =====
build_prompt() {
  cat <<'PROMPT_HEADER'
다음은 오늘 여러 repository의 git commit 로그다.
아래 표준 양식의 일일 업무일지로 변환해라.

[양식 규칙]
- 첫 줄: # Worklog YYYY-MM-DD
- 인용구로 메타데이터: > author: ..., > tags: #t1 #t2 ...
- ## Summary: 2~3줄 산문 요약 (한국어)
- ## Projects: 카테고리 사전에서만 선택해 ### 헤딩으로 사용
  - 각 작업: - [x] 한 줄 설명 + 인라인 태그(`#tag`)
  - 진행 없는 카테고리는 _(진행 없음)_
- ## Repositories: 각 레포를 #### 헤딩, 커밋 short hash + subject 나열
- ## Notes: 회의/외근/메모 등 작업 외 사항 (없으면 섹션 생략)

[분류 규칙]
1. repo_mapping에서 해당 repo를 찾는다
2. overrides가 있으면 commit subject prefix 매칭 우선 적용
3. 매칭 실패 시 default 사용
4. repo가 사전에 없으면 unclassified 분류

[출력]
- 순수 마크다운만
- 코드 블록(```)으로 전체를 감싸지 말 것
- 사족/설명/안내문 일절 금지
- 한국어로 작성

PROMPT_HEADER

  if [[ -n "$VOCAB" ]]; then
    echo "[프로젝트 매핑 사전]"
    cat "$VOCAB"
    echo ""
  fi

  echo "[입력 데이터]"
  cat "$INPUT"
}

# ===== Claude 호출 (구독 OAuth) =====
call_claude() {
  command -v claude >/dev/null 2>&1 || { echo "  claude not installed" >&2; return 1; }

  local prompt_file out
  prompt_file=$(mktemp)
  build_prompt > "$prompt_file"

  out=$(cd "$WORK_DIR" && \
    timeout "$TIMEOUT_SEC" claude -p "$(cat "$prompt_file")" \
      --output-format text \
      --allowedTools "" \
      2>/tmp/refine-claude.err) || {
        echo "  claude failed (see /tmp/refine-claude.err)" >&2
        rm -f "$prompt_file"
        return 1
      }

  rm -f "$prompt_file"
  [[ -n "$out" ]] || { echo "  claude returned empty output" >&2; return 1; }
  printf '%s\n' "$out"
}

# ===== Gemini 호출 (Google OAuth) =====
call_gemini() {
  command -v gemini >/dev/null 2>&1 || { echo "  gemini not installed" >&2; return 1; }

  local out
  out=$(cd "$WORK_DIR" && \
    build_prompt | timeout "$TIMEOUT_SEC" gemini -p --output-format text 2>/tmp/refine-gemini.err) \
    || {
        echo "  gemini failed (see /tmp/refine-gemini.err)" >&2
        return 1
      }

  [[ -n "$out" ]] || { echo "  gemini returned empty output" >&2; return 1; }
  printf '%s\n' "$out"
}

# ===== fallback chain =====
echo "refining: $INPUT" >&2
result=""
used=""

if result=$(call_claude); then
  used="claude"
elif result=$(call_gemini); then
  used="gemini"
else
  echo "ERROR: all LLM backends failed. Saving raw input as fallback." >&2
  cp "$INPUT" "$OUTPUT"
  exit 2
fi

printf '%s\n' "$result" > "$OUTPUT"
echo "wrote: $OUTPUT (engine=$used)"
