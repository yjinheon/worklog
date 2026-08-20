# 워크로그 → Jira 런북

git commit → 일일 워크로그 → Jira 이슈로 이어지는 하루 흐름 정리.

## 매일 하는 일

```bash
# 1. 워크로그 생성 (git log를 LLM이 정리)
./hh_worklog.py 2026-08-19 -o worklogs/2026-08-19.md

# 2. 생성 계획 확인 (Jira에 쓰지 않음)
just jira-plan 2026-08-19 2026-08-19

# 3. 계획 파일을 눈으로 확인하고 필요하면 수정
$EDITOR jira_plan_20260819_20260819.yaml

# 4. 적용
./jira_create.py --apply --yes --from-plan jira_plan_20260819_20260819.yaml
```

3번을 건너뛰고 `just jira-apply 2026-08-19 2026-08-19` 로 바로 만들 수도 있지만,
그 경우 계획을 확인할 기회가 없다.

## 사전 준비

토큰은 `config.yaml`의 `CONFLUENCE_TOKEN`을 그대로 쓴다.
환경변수 `JIRA_API_TOKEN`이 있으면 그쪽이 우선한다.

```bash
export JIRA_API_TOKEN=...   # 선택
```

## 만들어지는 구조

```
Epic (내가 담당, 미리 존재해야 함)
└─ Task            ← 프로젝트별 묶음, 또는 독립 단위 업무
   └─ 하위 작업     ← 기본 레벨
```

- 기본은 **하위 작업**이다. 범위가 크고 후속 작업이 따라오는 것만 독립 Task로 올라간다
- 한 (프로젝트 + Epic) 묶음이 `min_group`(기본 2) 미만이면 묶음 Task 없이 평범한 Task가 된다
- 이슈명에는 Epic 이름에서 읽은 `[Data]` / `[AI]` 접두가 붙는다
- 실제 작업 기간은 `시작 날짜`(customfield_10015)와 `기한`(duedate)에 들어간다.
  Jira는 `created` / `resolutiondate` 소급 기록을 허용하지 않는다

## 계획 파일

```yaml
tasks:
- summary: '[Data] datalab-hub 작업'
  epic: DC-592
  subtasks:
  - key: 789b47252409026b
    summary: '[Data] dim_tas_user 스냅샷 컬럼 리네임 반영'
```

| 하고 싶은 것 | 방법 |
|---|---|
| 다른 Task 아래로 옮기기 | `subtasks` 항목을 통째로 다른 task 아래로 이동 |
| 이슈명 바꾸기 | `summary` 수정 |
| 만들지 않기 | 해당 줄 삭제 |
| Task를 쪼개기 | task 항목을 복사해 `summary`를 바꾸고 subtasks를 나눠 담기 |
| 다른 Epic으로 | `epic` 값 변경 (내가 담당인 Epic만 가능) |

`key`는 워크로그 줄과 이어주는 식별자다. 바꾸면 적용이 거부된다.

## 무엇이 걸러지는가

| 단계 | 기준 | 끄는 법 |
|---|---|---|
| 이미 추적 중 | 워크로그 줄에 이슈 링크가 있음 | — |
| 태그 필터 | `chore` / `style` / `ci` 태그만 붙은 줄 | `--include-trivial` |
| LLM 판정 | 설정값 변경, 문서 정리 등 너무 사소한 것 | `--no-triage` |
| Epic 미배정 | `epic_map`에 없는 프로젝트 | 리포트에 unresolved로 표시 |

LLM 판정이 실패하면 **전부 생성 쪽으로 남긴다**. 업무를 조용히 누락시키지 않기 위해서다.

## 생성 후

- 워크로그 원본 줄 끝에 이슈 링크가 붙는다
  `... #fix [DC-975](https://humuson.atlassian.net/browse/DC-975)`
- 이미 링크가 있는 줄은 다시 만들지도, 다시 쓰지도 않는다
- 생성 기록은 `.jira_created.jsonl`에 남는다 (git 추적 제외)
- 중간에 실패하면 만든 것까지 기록하고 멈춘다. 같은 명령을 다시 돌리면 남은 것만 이어서 만든다

## 설정 (`config.yaml`)

```yaml
JIRA_PROJECT_KEY: DC
worklog_dir: worklogs
skip_tags: [chore, style, ci]   # 이 태그만 붙은 줄은 이슈로 만들지 않음
min_group: 2                    # 이보다 적으면 묶음 Task 없이 평범한 Task

epic_map:
  data-engineering: DC-592      # group 단위
  ai-engineering: DC-974
  operations: DC-309
  worklog-system: null          # 생성 제외
  data-orchestrator: DC-309     # project 단위 (group보다 우선)
```

`epic_map` 값은 Epic key, Epic 이름(부분일치), `null` 중 하나다.
키에 오타가 있으면 생성 전에 에러로 멈춘다.

## 자주 막히는 곳

**`no epics assigned to the current user`**
Jira에서 본인이 담당(assignee)인 Epic이 없다. Epic을 먼저 만들거나 담당자를 본인으로 바꾼다.

**`epic_map[...] matches no epic` / `matches multiple epics`**
이름 매칭이 0건이거나 2건 이상이다. Epic key(`DC-309`)로 직접 지정하는 편이 안전하다.

**`plan refers to N worklog key(s) not found`**
계획 파일을 만든 뒤 워크로그를 다시 생성했다. 워크로그가 바뀌면 key도 바뀌므로 계획을 새로 만든다.

**워크로그를 다시 생성하면 이슈 링크가 사라진다**
LLM이 매번 다르게 요약하므로 key가 달라져 링크를 복원할 수 없다.
이슈를 이미 만든 날짜의 워크로그는 다시 생성하지 않는다.

## 관련 파일

| 파일 | 역할 |
|---|---|
| `daily_worklog.py` | repo들의 git log를 raw 마크다운으로 추출 |
| `hh_worklog.py` | raw → 일일 워크로그 (LLM) |
| `worklog_parser.py` | 워크로그 → 작업 항목 |
| `work_triage.py` | 항목별 레벨 판정 (task / subtask / skip) |
| `epic_classifier.py` | 항목 → Epic 배정 |
| `issue_plan.py` | 계획 파일 생성·검증 |
| `jira_issue.py` | 이슈 payload 구성 |
| `jira_create.py` | 전체 흐름과 Jira 호출 |
| `jira_done.py` | 완료 이슈를 CSV로 내보내기 (역방향) |
