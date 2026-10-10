from __future__ import annotations

import importlib
import unittest
from datetime import date
from pathlib import Path

from worklog_parser import WorkItem

sync = importlib.import_module("03_sync_obsidian")


def make_item(**kwargs) -> WorkItem:
    base = dict(
        summary="테스트 작업",
        project="demo",
        source_date=date(2026, 5, 4),
    )
    base.update(kwargs)
    return WorkItem(**base)


class RenderTaskLineTest(unittest.TestCase):
    def test_completed_line_has_due_and_completion(self) -> None:
        line = sync.render_task_line(make_item(tags=("fix", "clickhouse")), date(2026, 5, 4))
        self.assertEqual(
            line,
            "- [x] 테스트 작업 #project/demo #todo #fix [due:: 2026-05-04] [completion:: 2026-05-04]",
        )

    def test_open_line_has_due_only(self) -> None:
        line = sync.render_task_line(
            make_item(done=False, tags=("feat",)), date(2026, 5, 4)
        )
        self.assertIn("[due:: 2026-05-04]", line)
        self.assertNotIn("[completion::", line)

    def test_issue_key_appended(self) -> None:
        line = sync.render_task_line(
            make_item(issue_key="DL-123"), date(2026, 5, 4)
        )
        self.assertTrue(line.endswith("[issue:: DL-123]"))

    def test_bracket_type_wins_over_tags(self) -> None:
        line = sync.render_task_line(
            make_item(tags=("refactor",)), date(2026, 5, 4), bracket_types="fix, clickhouse"
        )
        self.assertIn("#todo #fix", line)


class ParseWorklogTest(unittest.TestCase):
    def test_t_format_uses_bracket_types_and_cleans_summary(self) -> None:
        text = (
            "2026-07-20\n\n작업완료 사항\n\n"
            "- [demo] [perf, airflow] extract 동시성 상향 #project/demo #perf #airflow\n"
            "- [demo] [fix] 토큰 갱신 #project/demo #fix\n"
        )
        parsed = sync.parse_worklog(text, date(2026, 7, 20))
        self.assertEqual(len(parsed.project_tasks), 2)
        self.assertEqual(
            parsed.project_tasks[0],
            "- [x] extract 동시성 상향 #project/demo #todo #perf [due:: 2026-07-20] [completion:: 2026-07-20]",
        )
        self.assertIn("토큰 갱신 #project/demo #todo #fix", parsed.project_tasks[1])

    def test_r_format_falls_back_to_commit_worklog(self) -> None:
        original = "2026-09-15\n\nContext\n\n...\n"
        commit = (
            "## 2026-09-15\n\n### Projects\n\n#### demo\n"
            "- [x] 작업 줄 #project/demo #todo #fix [due:: 2026-09-15] [completion:: 2026-09-15]\n"
        )
        parsed = sync.parse_worklog(original, date(2026, 9, 15), commit_worklog=commit)
        self.assertEqual(
            parsed.project_tasks,
            ["- [x] 작업 줄 #project/demo #todo #fix [due:: 2026-09-15] [completion:: 2026-09-15]"],
        )

    def test_highlights_retagged(self) -> None:
        text = (
            "### Highlights\n\n#### demo\n- 문제 — 조치 — 결과 1→2\n\n"
        )
        parsed = sync.parse_worklog(text, date(2026, 10, 8))
        self.assertEqual(
            parsed.highlights_by_project,
            {"demo": ["- #highlight #project/demo 문제 — 조치 — 결과 1→2"]},
        )


class ReportBodyTest(unittest.TestCase):
    def test_t_format_cuts_completed_section(self) -> None:
        text = "2026-07-20\n\n이슈사항\n\n- 문제\n\n작업완료 사항\n\n- [demo] [fix] a #project/demo\n"
        body = sync.report_body(text)
        self.assertIn("- 문제", body)
        self.assertNotIn("작업완료", body)
        self.assertNotIn("[demo]", body)

    def test_projects_format_keeps_summary_only(self) -> None:
        text = (
            "## 2026-04-16\n\n### Summary\n- 요약\n\n### Projects\n\n#### demo\n"
            "- [x] a #project/demo\n\n### Repositories\n\n#### demo\n- sha msg\n"
        )
        self.assertEqual(sync.report_body(text), "- 요약")

    def test_projects_with_colon_heading(self) -> None:
        text = "### Summary:\n- 요약\n\n### Projects:\n#### demo\n- [x] a\n"
        self.assertEqual(sync.report_body(text), "- 요약")

    def test_full_day_keeps_upper_half_with_highlights(self) -> None:
        text = (
            "2026-10-08\n\nContext\n\n내용\n\n---\n\n"
            "## 2026-10-08\n\n### Summary\n- x\n\n### Projects\n\n#### demo\n- [x] a\n"
        )
        body = sync.report_body(text)
        self.assertIn("Context", body)
        self.assertNotIn("### Projects", body)


class DailyNoteTest(unittest.TestCase):
    TEMPLATE = (
        "---\n# {{date}}\n\n## 01. TODO LIST\n\n### Today\n\n**오늘 작성한  할일**\n\n"
        "- [ ] #todo Workout #health [due:: today]\n\n### Due Today\n\n## 03. Work\n\n## 04. Track\n\n"
        "[[{{yesterday}}|< yesterday]] | [[{{tomorrow}}|tomorrow >]]\n"
    )
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        template_dir = self.tmp / "02.Area" / "template"
        template_dir.mkdir(parents=True)
        (template_dir / "daily-log-template.md").write_text(self.TEMPLATE, encoding="utf-8")
        sync._daily_template_cache = None

    def test_new_note_renders_template(self) -> None:
        note = sync.new_daily_note(self.tmp, date(2026, 5, 4))
        self.assertIn("# 2026-05-04", note)
        self.assertIn("[[2026-05-03|< yesterday]]", note)
        self.assertIn("[[2026-05-05|tomorrow >]]", note)

    def test_resolve_finds_nested_existing_note(self) -> None:
        (self.tmp / "02.Area" / "daily-log").mkdir(parents=True)
        nested = self.tmp / "02.Area" / "daily-log" / "2026" / "2026-05"
        nested.mkdir(parents=True)
        (nested / "2026-05-04.md").write_text("x", encoding="utf-8")
        self.assertEqual(
            sync.resolve_daily_note(self.tmp, date(2026, 5, 4)), nested / "2026-05-04.md"
        )
        self.assertEqual(
            sync.resolve_daily_note(self.tmp, date(2026, 9, 15)),
            self.tmp / "02.Area" / "daily-log" / "2026-09-15.md",
        )

    def test_sync_replaces_same_date_block(self) -> None:
        note = sync.new_daily_note(self.tmp, date(2026, 5, 4))
        task = sync.build_task_block(date(2026, 5, 4), ["- [x] a #project/demo #todo #fix"])
        report = sync.build_report_block(date(2026, 5, 4), "본문 1", {})
        note = sync.sync_daily_note(note, date(2026, 5, 4), task, report)
        note = sync.sync_daily_note(
            note,
            date(2026, 5, 4),
            sync.build_task_block(date(2026, 5, 4), ["- [x] b #project/demo #todo #fix"]),
            sync.build_report_block(date(2026, 5, 4), "본문 2", {}),
        )
        self.assertIn("- [x] b #project/demo", note)
        self.assertNotIn("- [x] a #project/demo", note)
        self.assertIn("본문 2", note)
        self.assertNotIn("본문 1", note)
        self.assertEqual(note.count("worklog:auto:end %%"), 1)
        self.assertEqual(note.count("worklog:report:end %%"), 1)

    def test_sync_is_idempotent_when_report_has_headings(self) -> None:
        note = sync.new_daily_note(self.tmp, date(2026, 5, 4))
        # 2026-05-04 raw 덤프와 같은 구조: report 본문 안에 `## ` 헤딩이 있다
        body = (
            "# Worklog raw 2026-05-04\n\n"
            "_author: jinheonyoon_\n\n"
            "## cdp-pipeline (1 commits)\n\n"
            "```\n32752c9 perf: query hint 수정\n```\n\n"
            "## dagster-pipeline (9 commits)\n\n"
            "```\n836027f fix: tas_master 로그인 로그 필터링\n```"
        )
        task = sync.build_task_block(
            date(2026, 5, 4), ["- [x] a #project/cdp-pipeline #todo #perf"]
        )
        report = sync.build_report_block(date(2026, 5, 4), body, {})
        once = sync.sync_daily_note(note, date(2026, 5, 4), task, report)
        twice = sync.sync_daily_note(once, date(2026, 5, 4), task, report)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("worklog:report:start"), 1)
        self.assertEqual(once.count("worklog:report:end %%"), 1)
        self.assertIn("## 04. Track", once)
        self.assertIn("- [ ] #todo Workout #health [due:: today]", once)

    def test_sync_keeps_user_content(self) -> None:
        note = sync.new_daily_note(self.tmp, date(2026, 5, 4))
        task = sync.build_task_block(date(2026, 5, 4), ["- [x] a #project/demo #todo #fix"])
        note = sync.sync_daily_note(note, date(2026, 5, 4), task, "")
        self.assertIn("- [ ] #todo Workout #health [due:: today]", note)
        self.assertIn("## 04. Track", note)


class ProjectNoteTest(unittest.TestCase):
    def test_template_has_goal_sort_and_highlights_query(self) -> None:
        note = sync.new_project_todo("demo", date(2026, 10, 10))
        self.assertIn('FROM #project/demo', note)
        self.assertIn("SORT T.completion DESC", note)
        self.assertIn('FROM #highlight AND #project/demo', note)
        self.assertIn('contains(L.tags, "#project/demo")', note)

    def test_find_project_note_falls_back_to_todo_md(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        (tmp / "01.Project" / "database").mkdir(parents=True)
        (tmp / "01.Project" / "database" / "todo.md").write_text("x", encoding="utf-8")
        self.assertEqual(
            sync.find_project_note(tmp, "database"), tmp / "01.Project" / "database" / "todo.md"
        )
        self.assertEqual(
            sync.find_project_note(tmp, "newproj"),
            tmp / "01.Project" / "newproj" / "newproj_todo.md",
        )

    def test_archived_project_not_recreated(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        (tmp / "04.Archive" / "archived_project" / "lazyflow").mkdir(parents=True)
        self.assertTrue(sync.is_archived_project(tmp, "lazyflow"))
        self.assertFalse(sync.is_archived_project(tmp, "worklog"))


class MainSyncTest(unittest.TestCase):
    """main() 통합: 플래그 없이도 daily note에 쓰고, 01.Project에는 안 쓴다."""

    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        template_dir = self.tmp / "02.Area" / "template"
        template_dir.mkdir(parents=True)
        (template_dir / "daily-log-template.md").write_text(
            DailyNoteTest.TEMPLATE, encoding="utf-8"
        )
        # 기존 01.Project 노트 (읽어야만 한다 — 쓰기 금지)
        (self.tmp / "01.Project" / "demo").mkdir(parents=True)
        self.project_note = self.tmp / "01.Project" / "demo" / "demo_todo.md"
        self.project_note.write_text("---\ntitle: demo\n---\n\n## Goal\n사용자 내용\n", encoding="utf-8")
        # 아카이브된 프로젝트
        (self.tmp / "04.Archive" / "archived_project" / "old-proj").mkdir(parents=True)
        self._known = sync.KNOWN_PROJECTS
        sync.KNOWN_PROJECTS = {"demo", "fresh", "old-proj"}
        sync._daily_template_cache = None

    def tearDown(self) -> None:
        sync.KNOWN_PROJECTS = self._known

    def _run(self, *extra: str) -> str:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(
                sync.main(
                    [
                        "2026-05-04",
                        "--skip-run-worklog",
                        "--worklog-path",
                        str(self.worklog),
                        "--vault-root",
                        str(self.tmp),
                        *extra,
                    ]
                ),
                0,
            )
        return buf.getvalue()

    def test_daily_note_written_by_default_and_idempotent(self) -> None:
        self.worklog = self.tmp / "worklog.md"
        self.worklog.write_text(
            "2026-05-04\n\n이슈사항\n\n- 문제\n\n작업완료 사항\n\n"
            "- [demo] [fix] a #project/demo #fix\n",
            encoding="utf-8",
        )
        out = self._run()
        note = self.tmp / "02.Area" / "daily-log" / "2026-05-04.md"
        self.assertTrue(note.is_file())
        self.assertIn("- [x] a #project/demo #todo #fix", note.read_text(encoding="utf-8"))
        self.assertIn("worklog:report:start", note.read_text(encoding="utf-8"))
        self.assertIn("- 문제", note.read_text(encoding="utf-8"))
        self.assertIn("wrote:", out)

        out2 = self._run()
        self.assertEqual(out2, "no changes\n")

    def test_project_note_not_touched_new_project_created(self) -> None:
        self.worklog = self.tmp / "worklog.md"
        self.worklog.write_text(
            "2026-05-04\n\n작업완료 사항\n\n"
            "- [demo] [fix] a #project/demo #fix\n"
            "- [fresh] [feat] b #project/fresh #feat\n"
            "- [old-proj] [fix] c #project/old-proj #fix\n",
            encoding="utf-8",
        )
        self._run()
        self.assertEqual(
            self.project_note.read_text(encoding="utf-8"),
            "---\ntitle: demo\n---\n\n## Goal\n사용자 내용\n",
        )
        fresh = self.tmp / "01.Project" / "fresh" / "fresh_todo.md"
        self.assertTrue(fresh.is_file())
        self.assertIn("SORT T.completion DESC", fresh.read_text(encoding="utf-8"))
        self.assertFalse((self.tmp / "01.Project" / "old-proj").exists())

    def test_dry_run_writes_nothing(self) -> None:
        self.worklog = self.tmp / "worklog.md"
        self.worklog.write_text(
            "2026-05-04\n\n작업완료 사항\n\n- [demo] [fix] a #project/demo #fix\n",
            encoding="utf-8",
        )
        out = self._run("--dry-run")
        self.assertIn("@@", out)
        self.assertFalse((self.tmp / "02.Area" / "daily-log" / "2026-05-04.md").is_file())


if __name__ == "__main__":
    unittest.main()
