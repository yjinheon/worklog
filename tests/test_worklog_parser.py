from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import worklog_parser

FORMAT_A = """## 2026-04-21

> author: jinheonyoon
> tags: #worklog #daily

### Summary
- 요약 한 줄

### Projects

#### cdp-pipeline

진행 중 작업
- [ ] site seq bucket 순차처리 설계 #project/cdp-pipeline #todo #feat  [due:: 2026-04-25]

완료된 작업
- [x] 메모리 explode 방지용 site seq bucket 수준 순차처리 적용 #project/cdp-pipeline #todo #feat  [due:: 2026-04-20]  [completion:: 2026-04-21]

#### dagster-pipeline

진행 중 작업
- _(진행 없음)_

완료된 작업
- [x] sensored jobs 네이밍 리팩토링 #project/dagster-pipeline #todo #refactor [completion:: 2026-04-21]

### Repositories

#### cdp-pipeline
- 32752c9 perf: query hint 수정
- ab12cd3 feat: bucket 순차처리
"""

FORMAT_B = """2026-08-12

이슈사항

- data-orchestrator TAS 캠페인 마트 신규 구축 필요

검토사항

- 재적재 방식 검토

작업완료 사항

- [data-orchestrator] [feat, clickhouse] TAS 캠페인 마트 신규 구축: bronze 적재 및 tas_05 해체 #project/data-orchestrator #feat #clickhouse

- [dagster-pipeline] [feat] run 단위 감사로그 emit 추가 #project/dagster-pipeline #feat #dagster

- [회의] [meeting] 회의명/논의 내용/후속 조치 작성 필요 #project/회의 #meeting
"""

FORMAT_RAW = """# Worklog raw 2026-05-04

_author: jinheonyoon_

## cdp-pipeline (1 commits)

```
32752c9 perf: broadcaset hash join 수정
 2 files changed, 20 insertions(+), 18 deletions(-)
```
"""

TRUNCATED_B = """2026-07-07

이슈사항

- tkc user 파이프라인 gold PII 분기 필요

검토사항

- 복호화 경로 검토
"""


class FormatDetectionTests(unittest.TestCase):
    def test_detects_each_known_layout(self) -> None:
        self.assertEqual(worklog_parser.detect_format(FORMAT_A), "projects")
        self.assertEqual(worklog_parser.detect_format(FORMAT_B), "sections")
        self.assertEqual(worklog_parser.detect_format(FORMAT_RAW), "raw")
        self.assertEqual(worklog_parser.detect_format("아무 내용"), "unknown")


class FormatAParsingTests(unittest.TestCase):
    def items(self) -> list[worklog_parser.WorkItem]:
        return worklog_parser.parse_worklog(FORMAT_A, date(2026, 4, 21))

    def test_parses_checkbox_items_with_state_and_dates(self) -> None:
        items = self.items()

        self.assertEqual(len(items), 3)
        pending = items[0]
        self.assertFalse(pending.done)
        self.assertEqual(pending.summary, "site seq bucket 순차처리 설계")
        self.assertEqual(pending.project, "cdp-pipeline")
        self.assertEqual(pending.due_date, date(2026, 4, 25))
        self.assertIsNone(pending.completion_date)

        finished = items[1]
        self.assertTrue(finished.done)
        self.assertEqual(
            finished.summary, "메모리 explode 방지용 site seq bucket 수준 순차처리 적용"
        )
        self.assertEqual(finished.due_date, date(2026, 4, 20))
        self.assertEqual(finished.completion_date, date(2026, 4, 21))

    def test_strips_todo_and_project_tags_but_keeps_task_tags(self) -> None:
        self.assertEqual(self.items()[1].tags, ("feat",))

    def test_ignores_no_progress_placeholder(self) -> None:
        summaries = [item.summary for item in self.items()]
        self.assertNotIn("_(진행 없음)_", summaries)

    def test_collects_commit_shas_per_project(self) -> None:
        shas = worklog_parser.parse_commit_shas(FORMAT_A)
        self.assertEqual(shas, {"cdp-pipeline": ("32752c9", "ab12cd3")})


class FormatBParsingTests(unittest.TestCase):
    def items(self) -> list[worklog_parser.WorkItem]:
        return worklog_parser.parse_worklog(FORMAT_B, date(2026, 8, 12))

    def test_parses_completed_section_entries(self) -> None:
        items = self.items()

        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertTrue(first.done)
        self.assertEqual(first.project, "data-orchestrator")
        self.assertEqual(
            first.summary, "TAS 캠페인 마트 신규 구축: bronze 적재 및 tas_05 해체"
        )
        self.assertEqual(first.tags, ("feat", "clickhouse"))
        self.assertEqual(first.source_date, date(2026, 8, 12))

    def test_completion_date_defaults_to_none_for_section_format(self) -> None:
        self.assertIsNone(self.items()[0].completion_date)

    def test_skips_meeting_placeholder_line(self) -> None:
        self.assertNotIn("회의", [item.project for item in self.items()])

    def test_tolerates_missing_completed_section(self) -> None:
        self.assertEqual(
            worklog_parser.parse_worklog(TRUNCATED_B, date(2026, 7, 7)), []
        )


class RawFormatTests(unittest.TestCase):
    def test_raw_git_log_yields_no_items(self) -> None:
        self.assertEqual(
            worklog_parser.parse_worklog(FORMAT_RAW, date(2026, 5, 4)), []
        )


class FileLoadingTests(unittest.TestCase):
    def test_parse_file_derives_date_from_filename(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "2026-08-12.md"
            path.write_text(FORMAT_B, encoding="utf-8")
            items = worklog_parser.parse_worklog_file(path)

        self.assertEqual(items[0].source_date, date(2026, 8, 12))

    def test_load_range_reads_only_files_inside_range(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "2026-08-11.md").write_text(FORMAT_B, encoding="utf-8")
            (root / "2026-08-12.md").write_text(FORMAT_B, encoding="utf-8")
            (root / "2026-08-13.md").write_text(FORMAT_B, encoding="utf-8")
            (root / "notes.md").write_text(FORMAT_B, encoding="utf-8")

            items = worklog_parser.load_range(
                root, date(2026, 8, 11), date(2026, 8, 12)
            )

        self.assertEqual(
            sorted({item.source_date for item in items}),
            [date(2026, 8, 11), date(2026, 8, 12)],
        )


class IdentityTests(unittest.TestCase):
    def test_key_is_stable_and_distinguishes_items(self) -> None:
        items = worklog_parser.parse_worklog(FORMAT_B, date(2026, 8, 12))
        again = worklog_parser.parse_worklog(FORMAT_B, date(2026, 8, 12))

        self.assertEqual(items[0].key(), again[0].key())
        self.assertNotEqual(items[0].key(), items[1].key())
        self.assertEqual(len(items[0].key()), 16)

    def test_key_changes_with_source_date(self) -> None:
        one = worklog_parser.parse_worklog(FORMAT_B, date(2026, 8, 12))[0]
        two = worklog_parser.parse_worklog(FORMAT_B, date(2026, 8, 11))[0]

        self.assertNotEqual(one.key(), two.key())


HEADING_VARIANT_A = """## 2026-05-11

### Summary:
- 요약

### Projects:
#### data-orchestrator
진행 중 작업
_(진행 없음)_
완료된 작업
- [x] Teams 알림 변수 처리 오류 수정 #project/data-orchestrator #todo #fix [completion:: 2026-05-11]

### Repositories:
#### data-orchestrator
- 1a2b3c4 fix: teams alert
"""

HEADING_VARIANT_B = """2026-07-07

이슈사항

- 배경

작업사항

- [tkc-pipeline] [feat] gold PII 발행 분기 구현 #project/tkc-pipeline #feat
"""


class HeadingVariantTests(unittest.TestCase):
    def test_projects_heading_with_trailing_colon(self) -> None:
        items = worklog_parser.parse_worklog(HEADING_VARIANT_A, date(2026, 5, 11))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].project, "data-orchestrator")
        self.assertEqual(items[0].completion_date, date(2026, 5, 11))
        self.assertEqual(items[0].shas, ("1a2b3c4",))

    def test_completed_section_named_without_wanryo(self) -> None:
        items = worklog_parser.parse_worklog(HEADING_VARIANT_B, date(2026, 7, 7))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].project, "tkc-pipeline")
        self.assertEqual(items[0].summary, "gold PII 발행 분기 구현")

    def test_detects_both_variants(self) -> None:
        self.assertEqual(worklog_parser.detect_format(HEADING_VARIANT_A), "projects")
        self.assertEqual(worklog_parser.detect_format(HEADING_VARIANT_B), "sections")


ISSUE_TAGGED = """2026-08-12

작업완료 사항

- [data-orchestrator] [feat] TAS 마트 신설 #project/data-orchestrator #feat [issue:: DC-917]

- [dagster-pipeline] [fix] 감사로그 누락 수정 #project/dagster-pipeline #fix
"""

ISSUE_TAGGED_PROJECTS = """## 2026-04-21

### Projects

#### cdp-pipeline
완료된 작업
- [x] bucket 순차처리 적용 #project/cdp-pipeline #todo #feat [completion:: 2026-04-21] [issue:: DC-500]
"""


class IssueFieldTests(unittest.TestCase):
    def test_reads_issue_key_from_sections_layout(self) -> None:
        items = worklog_parser.parse_worklog(ISSUE_TAGGED, date(2026, 8, 12))

        self.assertEqual(items[0].issue_key, "DC-917")
        self.assertIsNone(items[1].issue_key)

    def test_reads_issue_key_from_projects_layout(self) -> None:
        items = worklog_parser.parse_worklog(ISSUE_TAGGED_PROJECTS, date(2026, 4, 21))

        self.assertEqual(items[0].issue_key, "DC-500")

    def test_issue_field_is_stripped_from_the_summary(self) -> None:
        items = worklog_parser.parse_worklog(ISSUE_TAGGED, date(2026, 8, 12))

        self.assertEqual(items[0].summary, "TAS 마트 신설")

    def test_issue_field_does_not_change_the_identity_key(self) -> None:
        tagged = worklog_parser.parse_worklog(ISSUE_TAGGED, date(2026, 8, 12))[0]
        untagged = worklog_parser.parse_worklog(
            ISSUE_TAGGED.replace(" [issue:: DC-917]", ""), date(2026, 8, 12)
        )[0]

        self.assertEqual(tagged.key(), untagged.key())


class SourceLocationTests(unittest.TestCase):
    def test_records_one_based_line_numbers(self) -> None:
        items = worklog_parser.parse_worklog(ISSUE_TAGGED, date(2026, 8, 12))
        lines = ISSUE_TAGGED.splitlines()

        self.assertIn("TAS 마트 신설", lines[items[0].source_line - 1])
        self.assertIn("감사로그 누락 수정", lines[items[1].source_line - 1])

    def test_records_line_numbers_in_projects_layout(self) -> None:
        items = worklog_parser.parse_worklog(
            ISSUE_TAGGED_PROJECTS, date(2026, 4, 21)
        )
        lines = ISSUE_TAGGED_PROJECTS.splitlines()

        self.assertIn("bucket 순차처리", lines[items[0].source_line - 1])

    def test_parse_file_records_source_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "2026-08-12.md"
            path.write_text(ISSUE_TAGGED, encoding="utf-8")
            items = worklog_parser.parse_worklog_file(path)

        self.assertEqual(items[0].source_path, path)

    def test_load_range_records_source_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "2026-08-12.md"
            path.write_text(ISSUE_TAGGED, encoding="utf-8")
            items = worklog_parser.load_range(root, date(2026, 8, 12), date(2026, 8, 12))

        self.assertEqual({item.source_path for item in items}, {path})


LINKED = """2026-08-12

작업완료 사항

- [data-orchestrator] [feat] TAS 마트 신설 #project/data-orchestrator #feat [DC-1023](https://humuson.atlassian.net/browse/DC-1023)

- [dagster-pipeline] [fix] 감사로그 수정 #project/dagster-pipeline #fix [issue:: DC-500]
"""


class IssueLinkTests(unittest.TestCase):
    """Both the markdown link and the plain field mark a line as tracked."""

    def items(self) -> list[worklog_parser.WorkItem]:
        return worklog_parser.parse_worklog(LINKED, date(2026, 8, 12))

    def test_reads_key_from_a_markdown_issue_link(self) -> None:
        self.assertEqual(self.items()[0].issue_key, "DC-1023")

    def test_still_reads_the_plain_field_form(self) -> None:
        self.assertEqual(self.items()[1].issue_key, "DC-500")

    def test_link_is_stripped_from_the_summary(self) -> None:
        self.assertEqual(self.items()[0].summary, "TAS 마트 신설")

    def test_link_does_not_change_the_identity_key(self) -> None:
        linked = self.items()[0]
        plain = worklog_parser.parse_worklog(
            LINKED.replace(
                " [DC-1023](https://humuson.atlassian.net/browse/DC-1023)", ""
            ),
            date(2026, 8, 12),
        )[0]

        self.assertEqual(linked.key(), plain.key())

    def test_ordinary_markdown_link_is_not_mistaken_for_an_issue(self) -> None:
        text = LINKED.replace(
            "[DC-1023](https://humuson.atlassian.net/browse/DC-1023)",
            "[문서](https://example.com/docs)",
        )
        self.assertIsNone(worklog_parser.parse_worklog(text, date(2026, 8, 12))[0].issue_key)


if __name__ == "__main__":
    unittest.main()
