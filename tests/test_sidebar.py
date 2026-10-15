"""Tests for the budgeted harness-sidebar payload.

Stdlib unittest like the rest of the suite.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from tidetable import cli, sidebar


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
                         ["current", "concurrent", "upcoming", "phase", "overall", "run"])
        self.assertIsNone(payload["run"])   # no run state passed -> nothing to show

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


class RunTest(unittest.TestCase):
    """The live-run banner: present only when a run is recorded."""

    def test_no_run_renders_no_section(self):
        text = sidebar.render_sidebar(sidebar.sidebar_payload(fixture()))
        self.assertNotIn("Run\n", text)

    def test_running_banner_shows_branch_and_last_event(self):
        run = {"status": "running", "date": "2026-10-14", "pid": 42,
               "branch": "tkrun/2026-10-14", "deadline": "03:05",
               "last": "START 1.2 'Do work'"}
        payload = sidebar.sidebar_payload(fixture(), run=run)
        self.assertEqual(payload["run"]["status"], "running")
        text = sidebar.render_sidebar(payload, width=60)
        self.assertIn("Run", text)
        self.assertIn("tkrun/2026-10-14", text)
        self.assertIn("START 1.2", text)

    def test_stale_and_finished_render_one_line(self):
        from tidetable import sidebar as sb
        stale = sb._run_rows({"status": "stale", "date": "2026-10-13", "pid": 9})
        self.assertEqual(len(stale), 1)
        self.assertIn("gone", stale[0])
        self.assertEqual(sb._run_rows(None), [])


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
            self.assertIn("run", payload)

    def test_sidebar_picks_up_a_local_run_dir(self):
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "todo.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            (root / ".tkrun").mkdir()
            (root / ".tkrun" / "log.md").write_text(
                "# log\n\n## 2026-10-14 run (pid 4242)\n\n"
                "- 2026-10-14 02:05:06 worktree /x on tkrun/2026-10-14; deadline 03:05\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(["sidebar", str(path), "--json"])
            self.assertEqual(code, 0)
            run = json.loads(buf.getvalue())["run"]
            self.assertEqual(run["branch"], "tkrun/2026-10-14")
            self.assertEqual(run["pid"], 4242)


if __name__ == "__main__":
    unittest.main()
