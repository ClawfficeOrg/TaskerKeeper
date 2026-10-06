"""Tests for the budgeted harness-sidebar payload.

Stdlib unittest like the rest of the suite.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from taskerkeeper import cli, sidebar


def fixture() -> dict:
    return {
        "schema_version": "1.0.0",
        "project": {
            "name": "Test",
            "version": {"milestone": "v1", "release_version": "0.1.0"},
            "description": "fixture",
        },
        "phases": [
            {
                "id": "1.0",
                "title": "Foundation",
                "goal": "goal",
                "tasks": [
                    {"id": "1.0.1", "title": "Done work", "status": "done"},
                    {"id": "1.0.2", "title": "Live work", "status": "in_progress",
                     "goal": "Half-written parser\nsecond line",
                     "claimed_by": "worker-1",
                     "claimed_at": "2026-09-23T10:00:00Z",
                     "lease_expires_at": "2026-09-23T11:00:00Z",
                     "touches": ["src/parser.rs"]},
                    {"id": "1.0.3", "title": "Same file", "status": "pending",
                     "touches": ["src/parser.rs"]},
                    {"id": "1.0.4", "title": "Other file", "status": "pending",
                     "touches": ["src/export.rs"]},
                ],
            },
            {
                "id": "1.1",
                "title": "Features",
                "goal": "goal",
                "prerequisites": ["1.0"],
                "tasks": [
                    {"id": "1.1.1", "title": "Later", "status": "pending",
                     "prerequisites": ["1.0.4"]},
                ],
            },
        ],
    }


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_sections_present_in_order(self):
        payload = sidebar.sidebar_payload(self.data)
        self.assertEqual(list(payload),
                         ["current", "concurrent", "upcoming", "phase", "overall"])

    def test_current_carries_claim_and_goal_first_line(self):
        (current,) = sidebar.sidebar_payload(self.data)["current"]["tasks"]
        self.assertEqual(current["id"], "1.0.2")
        self.assertEqual(current["owner"], "worker-1")
        self.assertNotIn("\n", current["goal"])
        # Basenames only: no directory leaks into a shared sidebar.
        self.assertEqual(current["touches"], ["parser.rs"])

    def test_concurrent_is_disjoint_set(self):
        conc = sidebar.sidebar_payload(self.data)["concurrent"]
        self.assertEqual([t["id"] for t in conc["tasks"]], ["1.0.4"])
        self.assertEqual(conc["deferred"], 1)

    def test_upcoming_holds_deferred_and_blocked(self):
        up = sidebar.sidebar_payload(self.data)["upcoming"]["tasks"]
        by_id = {t["id"]: t for t in up}
        self.assertIn("1.0.3", by_id)
        self.assertIn("1.1.1", by_id)
        self.assertTrue(by_id["1.1.1"]["blocked_by"])

    def test_phase_tree_anchors_on_live_work(self):
        phase = sidebar.sidebar_payload(self.data)["phase"]
        self.assertEqual(phase["id"], "1.0")
        self.assertEqual(len(phase["tree"]), 4)
        self.assertTrue(phase["tree"][1].startswith("[►] 1.0.2"))

    def test_overall_counts_done(self):
        overall = sidebar.sidebar_payload(self.data)["overall"]["tree"]
        self.assertIn("1/4 done", overall[0])
        self.assertIn("0/1 done", overall[1])

    def test_tight_height_reports_more_counts(self):
        payload = sidebar.sidebar_payload(self.data, height=10)
        mores = [payload[s]["more"] for s in ("current", "concurrent", "upcoming")]
        self.assertTrue(all(isinstance(m, int) for m in mores))
        self.assertEqual(payload["phase"]["more"], 1)  # 4 rows, 3-line budget
        self.assertEqual(payload["overall"]["more"], 0)

    def test_claim_age_buckets(self):
        now = datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(sidebar.claim_age({"claimed_at": "2026-09-23T12:00:00Z"}, now), "30m")
        self.assertEqual(sidebar.claim_age({"claimed_at": "2026-09-23T10:00:00Z"}, now), "2h")
        self.assertEqual(sidebar.claim_age({"claimed_at": "2026-09-20T12:30:00Z"}, now), "3d")
        self.assertEqual(sidebar.claim_age({}), "")


class RenderTest(unittest.TestCase):
    def test_text_fits_width(self):
        width = 40
        text = sidebar.render_sidebar(
            sidebar.sidebar_payload(fixture(), width=width), width)
        for line in text.splitlines():
            self.assertLessEqual(len(line), width, line)
        for title in ("Current", "Concurrent", "Upcoming", "Phase 1.0", "Overall"):
            self.assertIn(title, text)


class CommandTest(unittest.TestCase):
    def test_sidebar_json_command(self):
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "todo.json")
            Path(path).write_text(json.dumps(fixture()), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(["sidebar", path, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("concurrent", payload)


if __name__ == "__main__":
    unittest.main()
