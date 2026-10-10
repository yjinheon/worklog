from __future__ import annotations

import importlib
import unittest
from datetime import date

sync = importlib.import_module("03_sync_obsidian")
build_project_block = sync.build_project_block
new_project_todo = sync.new_project_todo
parse_worklog = sync.parse_worklog
sync_project_todo = sync.sync_project_todo



class SyncProjectTodoTest(unittest.TestCase):
    def test_new_date_appends_and_same_date_replaces(self) -> None:
        d1, d2 = date(2026, 10, 7), date(2026, 10, 8)
        note = new_project_todo("p")
        for day, task in ((d1, "a"), (d2, "b"), (d2, "c")):
            block = build_project_block(day, "p", [f"- [x] {task} #project/p"])
            note = sync_project_todo(note, day, "p", block)

        self.assertIn("- [x] a", note)  # earlier date kept
        self.assertNotIn("- [x] b", note)  # same date replaced
        self.assertIn("- [x] c", note)
        self.assertLess(note.index("date=2026-10-07"), note.index("date=2026-10-08"))
        self.assertEqual(note.count("worklog:auto:end"), 2)
        self.assertNotIn("#project/프로젝트명", note)

    def test_highlights_parsed_per_project_and_not_as_tasks(self) -> None:
        parsed = parse_worklog(
            "### Projects\n#### p\n- [x] t #project/p\n\n"
            "### Highlights\n#### p\n- 문제 — 조치 — 결과 9.7→1.6GiB\n\n"
            "### Repositories\n#### p\n- abc123 msg\n"
        )
        self.assertEqual(parsed.highlights_by_project, {"p": ["- 문제 — 조치 — 결과 9.7→1.6GiB"]})
        self.assertEqual(parsed.tasks_by_project, {"p": ["- [x] t #project/p"]})


if __name__ == "__main__":
    unittest.main()
