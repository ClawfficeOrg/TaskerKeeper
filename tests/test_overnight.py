"""Tests for `taskerkeeper overnight`, with a stub agent and a scratch git repo.

No model is ever called: OVERNIGHT_CLAUDE points at a python stub whose
behaviour STUB_MODE selects.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from taskerkeeper import cli, overnight

STUB = r'''
import os, sys, time
prompt = sys.stdin.read()
mode = os.environ.get("STUB_MODE", "done")
if "strict read-only reviewer" in prompt:
    print("REVIEW_RESULT: " + os.environ.get("STUB_REVIEW", "PASS"))
    sys.exit(0)
if mode == "limit":
    print("Claude usage limit reached|%d" % (int(time.time()) + 3600))
    sys.exit(1)
if mode == "silent":
    print("I did some things")
    sys.exit(0)
if mode != "nochange":
    open("work.txt", "w").write("work for " + prompt.split("Task: ")[1].split(" ")[0])
if os.environ.get("STUB_SIB_FILE"):
    os.makedirs(os.path.dirname(os.environ["STUB_SIB_FILE"]), exist_ok=True)
    open(os.environ["STUB_SIB_FILE"], "w").write("sib")
print("OVERNIGHT_SUMMARY: add work file")
print({"partial": "OVERNIGHT_RESULT: PARTIAL needs eyes",
       "failed": "OVERNIGHT_RESULT: FAILED cannot"}.get(mode, "OVERNIGHT_RESULT: DONE"))
'''


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout


class Scratch:
    """A git repo with a todo file, an overnight config and a stub agent."""

    def __init__(self, gate_exit: int = 0, tasks: int = 1, review: bool = False):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-b", "main")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        task_list = [{"id": f"1.{i}", "title": f"Task {i}", "status": "pending",
                      "goal": "make work.txt", "success": ["work.txt exists"],
                      "changelog": f"added {i}"} for i in range(1, tasks + 1)]
        todo = {"schema_version": "1.0.0",
                "project": {"name": "T", "version": {"milestone": "v1", "release_version": "0.1.0"},
                            "description": "fixture"},
                "phases": [{"id": "1", "title": "P", "goal": "g", "tasks": task_list}]}
        self.todo = self.root / "todo.json"
        self.todo.write_text(json.dumps(todo), encoding="utf-8")
        self.cfg_path = self.root / ".taskerkeeper" / "overnight.json"
        self.cfg_path.parent.mkdir()
        self.set_config({"gate": [{"name": "gate", "cmd": [sys.executable, "-c",
                                                           f"raise SystemExit({gate_exit})"]}],
                         "review": {"enabled": review}})
        stub = Path(self._tmp.name) / "stub.py"
        stub.write_text(STUB, encoding="utf-8")
        self.env = {"OVERNIGHT_CLAUDE": f'"{sys.executable}" "{stub}"',
                    "OVERNIGHT_RATE_MARGIN_SEC": "0"}

    def set_config(self, cfg: dict) -> None:
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        _git(self.root, "add", "-A")
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "cfg"], check=True,
                       capture_output=True)

    def run(self, mode: str = "done", *extra: str, review: str = "PASS", **env_extra) -> tuple[int, str]:
        env = {**self.env, "STUB_MODE": mode, "STUB_REVIEW": review, **env_extra}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(buf):
            code = cli.main(["overnight", "run", str(self.todo), "--model", "anthropic/stub",
                             *(extra or ("--hours", "1"))])
        return code, buf.getvalue()

    def status(self, tid: str = "1.1") -> str:
        """Task status as the run sees it: in the worktree once one exists."""
        todo = self.work / "todo.json" if self.work.exists() else self.todo
        data = json.loads(todo.read_text(encoding="utf-8"))
        return next(t["status"] for p in data["phases"] for t in p["tasks"] if t["id"] == tid)

    @property
    def work(self) -> Path:
        """The run's worktree, beside the repo."""
        return self.root.parent / f"{self.root.name}-overnight-{datetime.now():%Y-%m-%d}"

    @property
    def branch(self) -> str:
        return f"overnight/{datetime.now():%Y-%m-%d}"

    def close(self):
        self._tmp.cleanup()


class OvernightRunTests(unittest.TestCase):
    def scratch(self, **kw) -> Scratch:
        s = Scratch(**kw)
        self.addCleanup(s.close)
        return s

    def test_done_commits_on_branch_and_closes_task(self):
        s = self.scratch()
        base = _git(s.root, "rev-parse", "main").strip()
        code, out = s.run("done")
        self.assertEqual(code, 0, out)
        self.assertEqual(s.status(), "done")
        self.assertEqual(_git(s.root, "rev-parse", "main").strip(), base)
        # the checkout you started from is untouched; the work lives on the branch in the worktree
        self.assertEqual(_git(s.root, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertFalse((s.root / "work.txt").exists())
        self.assertEqual(json.loads(s.todo.read_text(encoding="utf-8"))["phases"][0]["tasks"][0]["status"],
                         "pending")
        self.assertEqual(_git(s.work, "rev-parse", "--abbrev-ref", "HEAD").strip(), s.branch)
        self.assertIn("1.1: add work file", _git(s.root, "log", s.branch, "--format=%s"))
        self.assertTrue((s.work / "work.txt").exists())
        self.assertEqual(_git(s.work, "status", "--porcelain").strip(), "")
        self.assertIn(str(s.work.name), out)

    def test_gate_failure_stashes_work_and_resets_task(self):
        s = self.scratch(gate_exit=1)
        _, out = s.run("done")
        self.assertEqual(s.status(), "pending")
        self.assertFalse((s.work / "work.txt").exists())
        self.assertIn("overnight", _git(s.work, "stash", "list"))
        self.assertIn("gate", out)

    def test_agent_failed_and_silent_agent_reset_task(self):
        for mode in ("failed", "silent"):
            with self.subTest(mode=mode):
                s = self.scratch()
                s.run(mode)
                self.assertEqual(s.status(), "pending")

    def test_partial_commits_wip_and_leaves_in_progress(self):
        s = self.scratch()
        s.run("partial")
        self.assertEqual(s.status(), "in_progress")
        self.assertIn("wip(1.1)", _git(s.root, "log", s.branch, "--format=%s"))

    def test_done_with_no_changes_is_a_failure(self):
        s = self.scratch()
        s.run("nochange")
        self.assertEqual(s.status(), "pending")

    def test_review_verdict_decides_whether_task_closes(self):
        s = self.scratch(review=True)
        s.run("done", review="FAIL some reason")
        self.assertEqual(s.status(), "in_progress")
        s2 = self.scratch(review=True)
        s2.run("done", review="PASS")
        self.assertEqual(s2.status(), "done")

    def test_required_changed_downgrades_done(self):
        s = self.scratch()
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "required_changed": ["CHANGELOG.md"], "review": {"enabled": False}})
        s.run("done")
        self.assertEqual(s.status(), "in_progress")

    def test_two_consecutive_failures_end_the_run(self):
        s = self.scratch(tasks=4)
        code, out = s.run("failed")
        self.assertEqual(code, 1)
        self.assertIn("2 consecutive failures", out)
        self.assertEqual(s.status("1.3"), "pending")

    def test_usage_limit_past_deadline_returns_task_to_pending(self):
        s = self.scratch()
        # the stub's reset is an hour away; the budget is ~0
        _, out = s.run("limit", "--hours", "0", "--minutes", "1")
        self.assertIn("RATE LIMIT", out)
        self.assertEqual(s.status(), "pending")

    def test_dirty_or_off_base_checkout_is_fine_because_the_run_uses_a_worktree(self):
        s = self.scratch()
        (s.root / "dirty.txt").write_text("x")
        _git(s.root, "switch", "-c", "feature")
        code, out = s.run("done")
        self.assertEqual(code, 0, out)
        self.assertEqual(s.status(), "done")
        self.assertEqual(_git(s.root, "rev-parse", "--abbrev-ref", "HEAD").strip(), "feature")
        self.assertTrue((s.root / "dirty.txt").exists())
        self.assertFalse((s.root / "work.txt").exists())

    def test_refuses_uncommitted_todo_changes(self):
        s = self.scratch()
        s.todo.write_text(s.todo.read_text(encoding="utf-8") + " ", encoding="utf-8")
        code, out = s.run("done")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes", out)

    def test_sibling_repo_is_a_worktree_too_and_real_checkout_is_untouched(self):
        s = self.scratch()
        sib = s.root.parent / "sib"
        sib.mkdir()
        _git(sib, "init", "-b", "work")
        _git(sib, "config", "user.email", "t@example.com")
        _git(sib, "config", "user.name", "T")
        (sib / "lib").mkdir()
        (sib / "lib" / "a.txt").write_text("a")
        _git(sib, "add", "-A")
        _git(sib, "commit", "-q", "-m", "init")
        s.set_config({"gate": [{"name": "g", "cmd": [sys.executable, "-c", "pass"]},
                               {"name": "sib-gate", "repo": "../sib", "when_repo_changed": "../sib",
                                "cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False}, "siblings": [{"path": "../sib", "scope": ["lib/"]}]})
        sib_wt = sib.parent / f"sib-overnight-{datetime.now():%Y-%m-%d}"
        code, out = s.run("done", STUB_SIB_FILE=str(sib_wt / "lib" / "b.txt"))
        self.assertEqual(code, 0, out)
        self.assertEqual(s.status(), "done")
        self.assertEqual(_git(sib, "rev-parse", "--abbrev-ref", "HEAD").strip(), "work")
        self.assertFalse((sib / "lib" / "b.txt").exists())
        self.assertTrue((sib_wt / "lib" / "b.txt").exists())
        self.assertIn("1.1", _git(sib, "log", s.branch, "--format=%s"))

    def test_second_run_resumes_the_same_worktree(self):
        s = self.scratch(tasks=2)
        s.run("done", "--hours", "1")
        self.assertEqual(s.status("1.2"), "done")
        code, out = s.run("done", "--hours", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("no more ready tasks", out)

    def test_stop_file_ends_run_before_first_task(self):
        s = self.scratch()
        (s.root / ".overnight").mkdir()
        (s.root / ".overnight" / "STOP").write_text("stop")
        code, out = s.run("done")
        self.assertEqual(code, 0)
        self.assertIn("STOP file", out)
        self.assertEqual(s.status(), "pending")

    def test_kill_file_ends_run_before_first_task(self):
        s = self.scratch()
        (s.root / ".overnight").mkdir()
        (s.root / ".overnight" / "KILL").write_text("kill")
        code, out = s.run("done")
        self.assertEqual(code, 0)
        self.assertIn("KILL file", out)
        self.assertEqual(s.status(), "pending")

    def test_kill_without_pid_file_writes_sentinel(self):
        import types

        s = self.scratch()
        code = overnight.cmd_kill(types.SimpleNamespace(todo_file=str(s.todo)))
        self.assertEqual(code, 0)
        self.assertTrue((s.root / ".overnight" / "KILL").exists())

    def test_kill_with_stale_pid_cleans_up(self):
        import types

        s = self.scratch()
        (s.root / ".overnight").mkdir()
        (s.root / ".overnight" / "pid").write_text("2147483647", encoding="ascii")
        code = overnight.cmd_kill(types.SimpleNamespace(todo_file=str(s.todo)))
        self.assertEqual(code, 0)
        self.assertTrue((s.root / ".overnight" / "KILL").exists())
        self.assertFalse((s.root / ".overnight" / "pid").exists())

    def test_dry_run_changes_nothing(self):
        s = self.scratch()
        code, out = s.run("done", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("OVERNIGHT_RESULT", out)
        self.assertEqual(_git(s.root, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertEqual(s.status(), "pending")

    def test_no_gate_and_no_review_refuses(self):
        s = self.scratch()
        s.set_config({"gate": [], "review": {"enabled": False}})
        code, out = s.run("done")
        self.assertEqual(code, 1)
        self.assertIn("nothing would verify", out)


class OvernightUnitTests(unittest.TestCase):
    def runner(self, **cfg):
        c = json.loads(json.dumps(overnight.DEFAULTS))
        c.update(cfg)
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "t.json").write_text("{}")
            return overnight.Runner(todo=root / "t.json", root=root, cfg=c, date="2026-01-01",
                                    deadline=datetime.now())

    def test_rate_limit_parsing(self):
        r = self.runner()
        r.rate_margin = 0
        now = datetime(2026, 1, 1, 10, 0, 0)
        self.assertIsNone(r.rate_limit_reset("all good", now))
        self.assertEqual(r.rate_limit_reset("HTTP 429", now), datetime(2026, 1, 1, 10, 15, 0))
        self.assertEqual(r.rate_limit_reset("limit reached, resets 3am", now),
                         datetime(2026, 1, 2, 3, 0, 0))
        self.assertEqual(r.rate_limit_reset("limit reached, resets at 14:30", now),
                         datetime(2026, 1, 1, 14, 30, 0))
        self.assertEqual(int(r.rate_limit_reset("usage limit reached|1900000000", now).timestamp()),
                         1900000000)

    def test_deadline_never_shortens_a_started_agent_run(self):
        r = self.runner()
        r.deadline = datetime.now()          # already past
        r.task_sec = 7200
        with TemporaryDirectory() as d:
            r.root = r.home = Path(d)
            r.run_dir = Path(d)
            seen = {}

            def fake(argv, cwd, stdin=None, timeout=0, env=None):
                seen["timeout"] = timeout
                return overnight.Proc(0, "OVERNIGHT_RESULT: DONE", "")
            with mock.patch.object(overnight, "run_proc", fake),                     mock.patch.object(r, "assert_safe"):
                r.run_agent("anthropic", "m", lambda _: "p", "t")
        self.assertEqual(seen["timeout"], 7200)

    def test_matches_globs_and_dirs(self):
        self.assertTrue(overnight.matches("src/a/b.rs", ["src/"]))
        self.assertTrue(overnight.matches("src/a/b.rs", ["*.rs"]))
        self.assertFalse(overnight.matches("docs/a.md", ["src/", "*.rs"]))

    def test_unknown_config_key_is_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".taskerkeeper").mkdir()
            (root / ".taskerkeeper" / "overnight.json").write_text('{"gates": []}')
            with self.assertRaises(ValueError):
                overnight.load_config(root)

    def test_stack_detection_gives_zero_config_gate(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "Cargo.toml").write_text("")
            cfg, path = overnight.load_config(root)
            self.assertIsNone(path)
            self.assertEqual([s["name"] for s in cfg["gate"]], ["clippy", "test"])
            self.assertIn("cargo test*", cfg["shell_allow"])

    def test_prompt_keeps_state_changes_with_the_runner(self):
        r = self.runner(rules=["custom rule"])
        p = r.build_prompt({"id": "9.9", "title": "T", "success": ["window looks right"]}, "a/b", False)
        self.assertIn("custom rule", p)
        self.assertIn("taskerkeeper\nstart/done/reset/status".replace("\n", " "), p)
        self.assertIn("window looks right", p.split("likely needing a human")[1])
        self.assertIn("git push*", r.shell_deny)


class LaneTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 12, 30, tzinfo=__import__("datetime").timezone.utc)

    @staticmethod
    def rsv(start, end, **kw):
        return {"starts_at": f"2026-10-07T{start}:00Z", "ends_at": f"2026-10-07T{end}:00Z", **kw}

    def test_window_merges_consecutive_hours_and_ignores_cancelled(self):
        rs = [self.rsv("12:00", "13:00"), self.rsv("13:00", "14:00"),
              self.rsv("14:00", "15:00", cancelled_at="2026-10-06T00:00:00Z"), self.rsv("16:00", "17:00")]
        s, e = overnight.lane_window(rs, self.NOW)
        self.assertEqual((s.hour, e.hour), (12, 14))

    def test_window_none_when_gap_or_empty(self):
        self.assertIsNone(overnight.lane_window([], self.NOW))
        self.assertIsNone(overnight.lane_window([self.rsv("13:00", "14:00")], self.NOW))
        self.assertIsNone(overnight.lane_window([self.rsv("11:00", "12:30")], self.NOW))

    def runner(self, reservations, margin=20, fallback="opencode-go/muse-spark-1.3-contributor"):
        c = json.loads(json.dumps(overnight.DEFAULTS))
        c["lanes"] = {"singularity": {"margin_minutes": margin, "fallback": fallback}}
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "t.json").write_text("{}")
            r = overnight.Runner(todo=root / "t.json", root=root, cfg=c, date="2026-01-01",
                                 deadline=datetime.now())
        r.lane_reservations = lambda lane: reservations
        return r

    def test_route_uses_lane_until_margin_then_falls_back(self):
        model = "deepseek-ai/DeepSeek-V4.1-Flash"
        with mock.patch.object(overnight, "datetime", wraps=datetime) as dt:
            dt.now.return_value = self.NOW
            dt.fromisoformat = datetime.fromisoformat
            r = self.runner([self.rsv("12:00", "13:00")], margin=20)       # 30 min left
            self.assertEqual(r.lane_route("singularity", model), ("singularity", model))
            r = self.runner([self.rsv("12:00", "13:00")], margin=40)       # inside the margin
            self.assertEqual(r.lane_route("singularity", model),
                             ("opencode-go", "muse-spark-1.3-contributor"))

    def test_route_falls_back_when_reservations_unreadable(self):
        r = self.runner([])
        def boom(lane):
            raise RuntimeError("no key")
        r.lane_reservations = boom
        self.assertEqual(r.lane_route("singularity", "m")[0], "opencode-go")
        self.assertEqual(r.lane_route("anthropic", "x"), ("anthropic", "x"))

    def test_fallback_is_required(self):
        with self.assertRaises(ValueError):
            self.runner([], fallback="")

    def test_agent_call_keeps_secrets_out_of_config(self):
        r = self.runner([])
        call = r.agent_call("singularity", "deepseek-ai/DeepSeek-V4.1-Flash", "do it")
        self.assertIn("singularity/deepseek-ai/DeepSeek-V4.1-Flash", call["argv"])
        cfg = json.loads(call["opencode"])
        opts = cfg["provider"]["singularity"]["options"]
        self.assertEqual(opts["apiKey"], "{env:SINGULARITY_LANE_APIKEY_SECRET}")
        self.assertEqual(opts["baseURL"], "{env:SINGULARITY_API_LANE_ENDPOINT}")
        self.assertEqual(cfg["provider"]["singularity"]["models"]["deepseek-ai/DeepSeek-V4.1-Flash"]
                         ["options"], {"reasoning_effort": "none"})


if __name__ == "__main__":
    unittest.main()
