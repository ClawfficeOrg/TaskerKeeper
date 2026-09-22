"""Tests for taskerkeeper.projector (task 1.2.1). Stdlib unittest only."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from taskerkeeper import cli, projector


def todo_data() -> dict:
    return {
        "schema_version": "1.0.0",
        "project": {
            "name": "T",
            "version": {"milestone": "v", "release_version": "0.1.0"},
            "description": "fixture",
        },
        "phases": [
            {
                "id": "1.0",
                "title": "P",
                "goal": "g",
                "tasks": [
                    {"id": "1.0.1", "title": "One", "status": "pending",
                     "touches": ["a.py"], "agent": "mid_dev_agent"},
                    {"id": "1.0.2", "title": "Two", "status": "done",
                     "touches": ["b/"]},
                ],
            }
        ],
    }


class ProjectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self) -> None:
        self.conn.close()

    def test_resnap_upserts_and_updates(self) -> None:
        data = todo_data()
        n = projector.resnap_todo(self.conn, "s", data, seq=7)
        self.assertEqual(n, 2)
        row = self.conn.execute(
            "SELECT status, title, touches, updated_seq FROM tasks_snap "
            "WHERE slug = ? AND task_id = ?", ("s", "1.0.1")).fetchone()
        self.assertEqual(row[0], "pending")
        self.assertEqual(row[1], "One")
        self.assertEqual(json.loads(row[2]), ["a.py"])
        self.assertEqual(row[3], 7)
        # Status change re-snaps the same row, no duplicate.
        data["phases"][0]["tasks"][0]["status"] = "done"
        n2 = projector.resnap_todo(self.conn, "s", data, seq=8)
        self.assertEqual(n2, 2)
        count = self.conn.execute("SELECT COUNT(*) FROM tasks_snap").fetchone()[0]
        self.assertEqual(count, 2)
        status = self.conn.execute(
            "SELECT status FROM tasks_snap WHERE task_id = ?", ("1.0.1",)).fetchone()[0]
        self.assertEqual(status, "done")

    def test_ingest_dedups_retail(self) -> None:
        events = [
            {"at": "2026-09-22T00:00:00Z", "event": "start", "task": "1.0.1",
             "owner": "w"},
            {"at": "2026-09-22T00:01:00Z", "event": "done", "task": "1.0.1",
             "owner": "w"},
        ]
        self.assertEqual(projector.ingest_events(self.conn, "s", events), 2)
        self.assertEqual(projector.ingest_events(self.conn, "s", events), 0)
        count = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 2)

    def test_project_once_and_write_then_query(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo_path = root / "todo.json"
            data = todo_data()
            todo_path.write_text(json.dumps(data), encoding="utf-8")
            registry = root / "registry.json"
            registry.write_text(json.dumps({"projects": [
                {"slug": "demo", "todo_path": "todo.json"}]}), encoding="utf-8")
            import os
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                report = projector.project_once(str(registry), self.conn)
            finally:
                os.chdir(cwd)
            self.assertEqual(report["demo"]["tasks"], 2)
            # Simulate a write then re-project: status flips in tasks_snap.
            disk = json.loads(todo_path.read_text(encoding="utf-8"))
            cli.set_status(disk, "1.0.1", "done")
            cli.save_todo(disk, str(todo_path))
            cli.record_event(str(todo_path), "done", task="1.0.1", owner="w")
            import os as _os
            cwd2 = _os.getcwd()
            _os.chdir(tmp)
            try:
                report2 = projector.project_once(str(registry), self.conn)
            finally:
                _os.chdir(cwd2)
            self.assertEqual(report2["demo"]["tasks"], 2)
            self.assertGreaterEqual(report2["demo"]["events"], 1)
            status = self.conn.execute(
                "SELECT status FROM tasks_snap WHERE slug = ? AND task_id = ?",
                ("demo", "1.0.1")).fetchone()[0]
            self.assertEqual(status, "done")

    def test_project_once_never_raises_on_bad_slug(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps({"projects": [
                {"slug": "ghost", "todo_path": "missing/todo.json"}]}), encoding="utf-8")
            report = projector.project_once(str(registry), self.conn)
            self.assertFalse(report["ghost"].get("ok", True))
            self.assertIn("error", report["ghost"])


if __name__ == "__main__":
    unittest.main()
