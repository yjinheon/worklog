import unittest

import jira_done


class JiraDoneTests(unittest.TestCase):
    def test_date_range_builds_inclusive_jql_and_default_output(self) -> None:
        period = jira_done.DateRange.parse("2026-06-01", "2026-06-30")

        self.assertEqual(
            period.jql(),
            'project = DC AND assignee = currentUser() '
            'AND resolutiondate >= "2026-06-01" '
            'AND resolutiondate < "2026-07-01" '
            'ORDER BY resolutiondate DESC',
        )
        self.assertEqual(
            period.default_output,
            "jira_done_20260601_20260630_selected.csv",
        )


if __name__ == "__main__":
    unittest.main()
