from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import hh_worklog_context


class HhWorklogContextTests(unittest.TestCase):
    def test_build_prompt_requests_context_before_existing_sections(self) -> None:
        prompt = hh_worklog_context.build_prompt(
            "# Worklog raw 2026-08-24\n\n작업 완료, 아직 커밋 전",
            "1. 오늘 발생한 이슈나 작업의 배경은 무엇인가요?",
            output_date="2026-08-24",
        )

        example = prompt.split("출력 예시:\n", 1)[1]
        self.assertLess(example.index("Context"), example.index("이슈사항"))
        self.assertLess(example.index("이슈사항"), example.index("검토사항"))
        self.assertLess(example.index("검토사항"), example.index("작업완료 사항"))
        self.assertIn("1~2문장", prompt)
        self.assertIn("bullet 없이", prompt)
        self.assertIn("오늘 무엇을 했는지와 현재 상태", prompt)
        self.assertIn("[project] [tag, tag] 업무 요약", prompt)

    def test_main_builds_context_prompt_and_prints_answer(self) -> None:
        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "worklog-2026-08-24.raw.md"
            spec_path = Path(tmpdir) / "hh_spec.md"
            raw_path.write_text("# raw context", encoding="utf-8")
            spec_path.write_text("1. 질문", encoding="utf-8")
            prompts: list[str] = []

            def fake_run_llm(prompt: str, **_: object) -> tuple[str, str]:
                prompts.append(prompt)
                return "2026-08-24\n\nContext\n\n요약\n", "claude"

            with (
                patch.object(hh_worklog_context, "run_llm", fake_run_llm),
                patch("sys.stdout") as stdout,
            ):
                rc = hh_worklog_context.main(
                    [
                        "hh_worklog_context.py",
                        str(raw_path),
                        "--spec",
                        str(spec_path),
                        "--config",
                        str(Path(tmpdir) / "missing.yaml"),
                        "--output",
                        "-",
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertIn("# raw context", prompts[0])
        self.assertIn("Context", prompts[0])
        stdout.write.assert_called_once_with("2026-08-24\n\nContext\n\n요약\n")


if __name__ == "__main__":
    unittest.main()
