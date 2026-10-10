# report-worklog — examples

Source of truth for these examples: `DAILY_LOG/DAILY_LOG.md` 2026-08-28 (3 sessions,
246 digest lines) → `worklogs/2026-08-28.md` (30 lines).

## 1. Input shape (extractor output, abridged)

```markdown
# DAILY_LOG digest 2026-08-28 (3 sessions)

## 2026-08-28 — Iceberg 카탈로그: HMS → REST 전환 검토와 P0 배포

### 요약
| 트랙 | 내용 | 결과 |
|---|---|---|
| HMS → REST 카탈로그 전환 검토 | 전환 가능성·범위·리스크 | 범위를 L1 로 확정. blocker 2 건은 조사 오류, 철회 |
| P0 설정 수렴 | 카탈로그 타입 스위치 + L1/L3 분리 | 완료·배포. 9 파일 +516/−254 |
...
### 3. 가장 위험한 지점 — `ops_03` × 29 테이블
REST 등록 후 두 카탈로그는 각자의 metadata pointer 를 갖는다. 여기서
`expire_snapshots`/`remove_orphan_files` 가 돌면 상대 카탈로그가 유효하다고
믿는 파일을 지운다. ...
### 이번 세션에서 얻은 함정 3 개      <- 제외
### 남은 것 · 주의                    <- 제외

## 2026-08-28 (세션 2) — tas 리팩터링 후속 마감과 공용 계층 재정의
...
## 2026-08-28 (세션 3) — tma/cafe24 통합 리팩터링과 공용 어휘 확립
...
```

## 2. 이슈사항 — 압축 전/후

증상 단위로 나열하면 자잘해지고 줄만 먹는다. **원인 단위로 묶고 증상은 em-dash 뒤로.**

Before (8 bullets, 증상 나열):

```markdown
- cafe24 쿠폰 DLQ 빌더 1개가 dict를 반환해 AttributeError 발생, 호출부 except가 삼켜 DLQ 적재 0건
- cafe24 products 커서의 전진 보장 부재로 동일 페이지 무한 루프, 조회 창의 KST 미변환으로 9시간 오차
- rest 카탈로그에서 hms_uri_default 폴백이 thrift 주소를 REST 엔드포인트로 전달
- Spark의 hive_metastore_uri 단일 필드가 세션 카탈로그와 iceberg 카탈로그를 동시에 지정
...
```

After (원인 단위로 병합):

```markdown
- cafe24 적재기 5종을 한자리에 모으자 개별 구현에 잠복해 있던 결함 3건 노출 — DLQ 계약 이탈로 적재 0건, 커서 전진 보장 부재로 무한 루프, 조회 창 KST 미변환 9시간 오차
- Iceberg 카탈로그 설정이 3개 엔진에 분산되고 겸용 상태 — hive 전용 폴백이 rest에서 thrift 주소를 REST 엔드포인트로 던지고, Spark 단일 필드가 세션 카탈로그와 iceberg 카탈로그를 동시에 지정
```

## 3. 검토사항 — 산문형/개조식

Before (서술형 문단, 5줄 소비):

```markdown
HMS→REST 전환은 범위를 L1로 좁혀 재조사한 결과, 초판 검토가 세운 blocker 2건이
모두 조사 오류로 판명돼 철회했다. 메타데이터 수집은 HMS가 아니라 Trino 경유였고 ...
```

After (명사형 종결, 1줄):

```markdown
- HMS→REST 전환 범위를 L1(Iceberg 카탈로그)로 축소, 초판 blocker 2건은 조사 오류로 판명돼 철회 — 메타데이터 수집 경로가 HMS 아닌 Trino였고 레거시 Hive와 Iceberg는 이미 카탈로그 단위로 분리
```

## 4. 작업완료 사항 — 좋은 불릿 / 나쁜 불릿

| 판정 | 불릿 |
|---|---|
| 나쁨 | `- Iceberg 카탈로그 설정을 대폭 개선하고 안정적으로 배포` (수치 0, 형용사만) |
| 좋음 | `- Iceberg 카탈로그 P0 TDD 구현·배포 — 3개 엔진에 분산된 설정을 단일 스위치로 수렴, 기본값 hive 유지로 런타임 무동작 설계(9파일 +516/−254, 신규 테스트 41건)` |
| 나쁨 | `- 배포 후 실패한 DAG를 확인함` (판정 없음) |
| 좋음 | `- 배포 직후 tma_09 실패를 태스크 상태(upstream_failed)와 배포 이전 24시간 이력 대조로 기존 외부 API 간헐 장애로 판별, 불필요한 롤백 회피` |

## 5. Final output

전체 결과물은 `worklogs/2026-08-28.md` 참조 — 30줄, 불릿 16개(이슈 6 · 검토 3 · 완료 7).
