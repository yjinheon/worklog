# Goal

git log / worklog를 역으로 읽어 Jira DC 프로젝트에 이슈를 생성하는 스크립트를 만든다.
`jira_done.py`(조회)의 역방향이며, 분류에 LLM 호출을 활용한다.

- issuetype: **Task**, 담당 **Epic의 하위**로 생성 (별도 요청 없으면 항상 이 형태)
- 날짜 소급: git log / worklog에서 추출한 실제 기간을 날짜 필드에 기록

# 확정된 설계 결정

## 1. 계층 구조

- 생성 이슈 issuetype = `작업`(Task)
- `fields.parent.key` = 담당 Epic key
- Sub-task는 만들지 않는다
- 대상 Epic 조회 JQL:
  ```
  project = DC AND issuetype = 에픽 AND assignee = currentUser()
  AND statusCategory != Done ORDER BY created DESC
  ```
  (`jira_done.py`의 resolutiondate 기반 JQL은 재사용 불가 — 새 쿼리)

## 2. 날짜 소급 방식

`created`/`resolutiondate`는 Jira API로 설정 불가. 대신 실제 존재가 확인된
쓰기 가능 날짜 필드를 사용한다 (출처: `jira_done_20260601_20260630_fields.json`).

| 의미 | 필드 | 값의 출처 |
|---|---|---|
| 시작일 | `customfield_10015` (시작 날짜, date) | 해당 작업의 **첫 커밋 날짜** |
| 완료일 | `duedate` (기한, date) | 해당 작업의 **마지막 커밋 날짜**, worklog `[completion:: ]`가 있으면 그 값 우선 |

- description 하단에 원본 근거 블록을 남긴다: 실제 시작/완료 일시, repo명, commit short sha 목록
- `customfield_10015`가 create screen에 없으면 400이 난다 →
  `/rest/api/3/issue/createmeta`로 사전 확인, 없으면 `customfield_10121/10122`(Target start/end)로 폴백
- 생성 직후 Done 전이는 하지 않는다(기본). `--transition-done` 옵션으로만 수행하며,
  이 경우 resolutiondate는 실행 시각으로 찍힌다는 점을 stdout에 경고한다.

## 3. 입력 소스

1순위: refine된 worklog 마크다운 `YYYY-MM-DD.md`
- 이미 커밋이 **이슈 단위로 묶여** 있고 `#project/<repo>` 태그를 갖는다
- 실제 파일은 한 형식이 아니라 **두 형식이 공존**하므로 파서가 둘 다 지원해야 한다:

  | 형식 | 기간 | 구조 | 날짜 정보 |
  |---|---|---|---|
  | `projects` | 04-16 ~ 06-26 (12개) | `### Projects` + `- [x] … #project/x` | `[due:: ]` / `[completion:: ]` 있음 |
  | `sections` | 07-07 ~ 08-12 (26개) | `작업완료 사항` + `- [proj] [tag] 요약 #project/x` | 없음 → 파일 날짜 사용 |

- 헤딩 변형도 존재: `### Projects:`(콜론), `작업사항`(= `작업완료 사항`)
- raw git log 덤프(`# Worklog raw`)는 작업 단위가 없으므로 **건너뛴다**
- `### Repositories` 섹션이 있으면 short sha를 수집해 description 근거로 사용
  (`sections` 형식에는 이 섹션이 없어 sha 근거가 비어 있다)

2순위(`--from-git`): `daily_worklog.py`가 만드는 raw 로그를 직접 입력
- 커밋 1:1 생성은 금지. LLM으로 반드시 작업 단위 묶음을 먼저 만든다.

입력 범위는 `--start` / `--end`로 지정(`jira_done.py`와 동일한 인자 규약).

## 4. Epic 분류 — 결정론 우선, LLM은 잔여분만

`config.yaml`에 매핑 테이블을 추가한다:

```yaml
epic_map:
  # group 단위 매핑
  data-engineering: DC-592                        # [Data] 내부 데이터 파이프라인 구축작업
  ai-engineering:   DC-829                        # [AI] LLM 서빙용 LiteLLM Gateway 환경 구축
  operations:       "데이터 오케스트레이터 고도화 및 일원화 작업"
  worklog-system:   null                          # 제외 — Jira 이슈를 만들지 않음
  # project 단위 오버라이드 (group보다 우선)
  data-orchestrator: "데이터 오케스트레이터 고도화 및 일원화 작업"
```

키는 **group 이름과 project 이름을 모두 허용**하며, project 항목이 group 항목보다 우선한다.
`data-orchestrator`는 config상 group이 `data-engineering`이라 group 규칙만으로는
파이프라인 Epic으로 가므로, project 오버라이드로 오케스트레이터 Epic에 배정한다.
실행 전 `validate_epic_map_keys()`로 모든 키가 알려진 group 또는 project인지 검사해
오타를 생성 전에 잡는다(`project_groups:`에 선언만 되고 아직 쓰이지 않는 group도 유효).

값 형식은 두 가지를 모두 허용한다:
- `DC-\d+` 패턴 → Epic key로 그대로 사용
- 그 외 문자열 → 담당 Epic 목록의 `summary`에서 **정규화 후 부분일치**로 해석
  (공백 압축 + 대소문자 무시, `[Data]` 같은 prefix 무시). `operations`는 로컬 데이터에
  key가 없어 이 경로를 탄다.
- 매칭 결과가 0개 또는 2개 이상이면 **에러로 중단**한다(임의 선택 금지).
  실행 초기에 한 번 검증해 잘못된 매핑을 생성 전에 잡는다.
- `null` → 해당 group은 생성 대상에서 제외. 스킵 건수를 리포트에 표시한다.

절차:
1. `epic_map`에 project 이름 항목이 있으면 그것을 사용 (LLM 호출 없음)
2. 없으면 `#project/<repo>` → `config.yaml`의 repo `group` → `epic_map` 조회
3. 매핑 값이 `null`이면 스킵(= `worklog-system`)
4. 매핑 실패분만 모아 LLM 1회 호출. 프롬프트에 현재 담당 Epic 목록(key + summary)을 제시하고 고르게 함
5. LLM이 고르지 못하면(`null`) 해당 작업은 **생성하지 않고** 미분류로 리포트에 남긴다 — 임의 Epic 배정 금지

LLM 백엔드는 `refine_worklog.py`의 `call_claude` / `call_gemini` / `call_kimi` 폴백 체인을 재사용한다.
출력은 자유 텍스트가 아니라 JSON 배열로 강제하고, `_clean_output()`을 거친 뒤
첫 `[` ~ 마지막 `]` 구간만 추출해 파싱한다(CLI stdout에 hook noise가 섞이는 기존 이슈 때문).
파싱 실패 시 그 배치는 전부 미분류 처리하고 중단하지 않는다.

## 5. 멱등성 — 중복 생성 방지 (필수)

- 상태 파일 `.jira_created.jsonl` (gitignore 대상)
  ```json
  {"key":"<sha256(worklog_line)[:16]>","issue_key":"DC-901","summary":"...","created_at":"..."}
  ```
- 실행 시 먼저 로드해 이미 생성된 key는 스킵
- 이슈 description 하단에도 `worklog-key: <key>` 를 남겨 상태 파일이 유실돼도 JQL로 복구 가능
- 부분 실패 시: 성공한 건은 즉시 append(버퍼링 금지) 후 종료 → 재실행하면 남은 것만 생성

## 6. 안전장치

- **기본 동작이 dry-run.** 실제 생성은 `--apply` 명시 필요
- dry-run은 생성될 payload(요약/parent/날짜/Epic)를 표로 출력
- `--apply` 시 생성 건수를 보여주고 확인 입력을 받음 (`--yes`로 생략)
- `--limit N`으로 1회 생성 상한
- 토큰: 생성 스크립트는 `JIRA_API_TOKEN` **환경변수만** 허용.
  `jira_done.py`의 `CONFLUENCE_TOKEN` legacy 폴백은 쓰기 경로에서 제거한다
- 에러 메시지에 Authorization 헤더/토큰이 노출되지 않도록 마스킹

# 구현 단계

Step 1~6 완료 및 2026-08-18 실적용 완료 (DC-975 ~ DC-986, 12건).

## Step 1 — 공용 클라이언트 추출 ✅
`jira_client.py` 신규:
- `JiraConfig`(`jira_done.py`에서 이동, `require_env_token: bool` 추가)
- `request(method, path, payload)` — base64 basic auth + urlopen + 에러 정규화
- `jira_done.py`는 이를 import하도록 수정 (동작 변경 없음, 기존 테스트 통과 유지)

## Step 2 — worklog 파서 ✅
`worklog_parser.py` 신규:
- `YYYY-MM-DD.md` → `WorkItem(summary, project, tags, start_date, done_date, shas)` 리스트
- `### Projects` 체크박스 라인과 `### Repositories` sha를 project명으로 조인
- 순수 함수, I/O 없음 → 단위 테스트 용이

## Step 3 — Epic 분류기 ✅
`epic_classifier.py` 신규:
- `classify(items, epic_map, repo_groups, epics) -> (assigned, unresolved)`
- LLM 호출은 주입 가능한 콜러블로 받아 테스트에서 대체

## Step 4 — 생성기 ✅
`jira_create.py` 신규 (uv script shebang, 기존 스크립트 규약 준수):
- `--start/--end/--config/--from-git/--apply/--yes/--limit/--transition-done`
- `/rest/api/3/myself`로 accountId 조회 (하드코딩 금지)
- `/rest/api/3/issue/createmeta`로 issuetype id와 날짜 필드 가용 여부 확인
- description은 **ADF(JSON)** 로 구성 — 평문 문자열은 400
- `POST /rest/api/3/issue`로 개별 생성, 429/5xx는 지수 백오프 재시도(최대 3회)

## Step 5 — 테스트 ✅
`tests/test_jira_create.py`, `tests/test_worklog_parser.py`, `tests/test_epic_classifier.py`
- `tests/test_confluence_export.py` / `test_jira_done.py`의 `urlopen` monkeypatch 패턴 그대로
- 필수 케이스: ADF 직렬화, 멱등성 스킵, 미분류 시 생성 안 함, dry-run이 네트워크 호출 0회,
  createmeta에 날짜 필드 없을 때 폴백, 부분 실패 후 재실행

## Step 6 — justfile 타겟 ✅
```
jira-plan start end:   ./jira_create.py --start {{start}} --end {{end}}
jira-apply start end:  ./jira_create.py --start {{start}} --end {{end}} --apply
```

# 실측으로 확정된 사항 (2026-08-18 라이브 검증)

- 인증: config.yaml의 `CONFLUENCE_TOKEN`으로 Jira 쓰기까지 동작. 쓰기에 환경변수를 강제하던
  제약은 사용자 결정으로 해제(`JIRA_API_TOKEN`이 있으면 그쪽이 우선).
- createmeta 응답 키는 `values`가 아니라 **`issueTypes` / `fields`**.
- hierarchyLevel 0에 스토리·작업·버그가 함께 있으므로 **이름으로 `작업`을 선택**해야 한다
  (레벨만 보면 스토리가 잡힌다). 선택 실패 시에만 레벨 폴백.
- JQL은 **번역된 타입명에 0건을 조용히 반환**한다(`issuetype = "에픽"` → 0건).
  타입 id로 조회한다(`issuetype = 10000`).
- 날짜 필드: `customfield_10015`(시작 날짜)와 `duedate`(기한) 모두 DC 작업 생성 화면에 존재.
  Target start/end(`customfield_10121/10122`)는 없음.
- 담당 Epic 4개: DC-974 [AI], DC-649 파이프라인 운영, DC-592 파이프라인 구축, DC-309 오케스트레이터 고도화.
- summary 255자 초과는 자동 절삭되고 전문은 description에 남는다.

# 이슈 링크 역기입

- 생성된 이슈는 워크로그 원본 줄에 markdown 링크로 역기입된다:
  `... #fix [DC-975](https://humuson.atlassian.net/browse/DC-975)`
- 이미 이슈가 표기된 줄(`[DC-123](.../browse/DC-123)` 또는 `[issue:: DC-123]`)은
  **생성도 역기입도 하지 않는다**. 재실행하면 `tracked=N`으로만 보고된다.
- hh_worklog.py 템플릿도 같은 링크 형식을 문서화하며, 근거가 없으면 생략하도록 지시한다.

# 열린 항목

- 4월~6월분(형식 A)에는 옛 프로젝트명(data-pipeline-refactor, tma-pipeline-mig, tkc-crm)이 있어
  61건이 unresolved로 남는다. 소급 생성하려면 `epic_map`에 project 항목으로 추가해야 한다.
- 과거 워크로그에 있는 `DC-917 Phase 4` 같은 본문 언급은 링크 형식이 아니라 tracked로 인식되지 않는다.
  소급 생성 시 중복이 우려되면 해당 줄에 `[issue:: DC-917]`을 먼저 넣을 것.
