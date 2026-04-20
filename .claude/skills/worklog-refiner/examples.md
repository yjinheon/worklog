# worklog-refiner — examples

## Input (raw.md)

```markdown
# Worklog raw 2026-04-20

## cdp-pipeline (3 commits)

​```
a1b2c3d feat(cdp): add user segment export to s3
e4f5g6h fix: null handling in dedup step
i7j8k9l refactor: split ingestion module
 3 files changed, 47 insertions(+), 8 deletions(-)
​```

## dagster-pipeline (2 commits)

​```
m1n2o3p feat(mon): add dagster asset freshness check
q4r5s6t docs: update runbook for pipeline monitoring
 2 files changed, 31 insertions(+), 4 deletions(-)
​```
```

## Output (refined worklog)

```markdown
# Worklog 2026-04-20

> author: jinheon
> tags: #cdp #monitoring #refactor

## Summary

- CDP 파이프라인 세그먼트 S3 익스포트 기능 추가 및 dedup null 처리 수정
- Dagster 자산 freshness 체크 도입으로 파이프라인 모니터링 강화

## Projects

### CDP 배치 파이프라인

- [x] user segment S3 익스포트 구현 #project/cdp-pipeline #todo #feat [completion:: 2026-04-20]
- [x] dedup 단계 null 처리 버그 수정 #project/cdp-pipeline #todo #fix [completion:: 2026-04-20]
- [x] ingestion 모듈 분리 #project/cdp-pipeline #todo #refactor [completion:: 2026-04-20]

### 데이터 파이프라인 모니터링 체계 구축

- [x] dagster asset freshness 체크 추가 #project/pipeline-monitoring #todo #feat [completion:: 2026-04-20]

### 데이터 파이프라인 리팩토링 및 통합

_(진행 없음)_

## Repositories

#### cdp-pipeline
- a1b2c3d feat(cdp): add user segment export to s3
- e4f5g6h fix: null handling in dedup step
- i7j8k9l refactor: split ingestion module

#### dagster-pipeline
- m1n2o3p feat(mon): add dagster asset freshness check
- q4r5s6t docs: update runbook for pipeline monitoring
```

## Notes on this example

- `dagster-pipeline`'s `feat(mon):` prefix is routed to `pipeline-monitoring` by the `overrides` rule in `vocab.yaml` — NOT to its `default` (`data-pipeline-refactor`).
- `docs` commits appear in `Repositories` but may be omitted from `Projects` task bullets at the LLM's discretion.
- Categories listed in `projects` but with no matching commits become `_(진행 없음)_`.
