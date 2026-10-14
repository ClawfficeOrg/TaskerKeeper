"""Tests for `tidetable overnight`, with a stub agent and a scratch git repo.

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

from tidetable import cli, overnight

STUB = r'''
import os, sys, time
prompt = sys.stdin.read()
mode = os.environ.get("STUB_MODE", "done")
if "strict read-only reviewer" in prompt:
    phase = "Every task of phase" in prompt
    if os.environ.get("STUB_LOG"):
        open(os.environ["STUB_LOG"], "a").write(("phase" if phase else "task") + "\n")
    key = "STUB_PHASE_REVIEW" if phase else "STUB_REVIEW"
    print("REVIEW_RESULT: " + os.environ.get(key, "PASS"))
    sys.exit(0)
if mode == "limit":
    print("Claude usage limit reached|%d" % (int(time.time()) + 3600))
    sys.exit(1)
if mode == "silent":
    print("I did some things")
    sys.exit(0)
tid = prompt.split("Task: ")[1].split(" ")[0]
if os.environ.get("STUB_ENVDUMP"):
    open(os.environ["STUB_ENVDUMP"], "w").write(
        "|".join(k for k in ("TIDETABLE_TOKEN", "TIDETABLE_SERVER", "KEEP_ME") if k in os.environ))
if os.environ.get("STUB_TRACK"):
    open(os.environ["STUB_TRACK"], "a").write("s %s %f\n" % (tid, time.time()))
    time.sleep(float(os.environ.get("STUB_SLEEP", "1.0")))
    open(os.environ["STUB_TRACK"], "a").write("e %s %f\n" % (tid, time.time()))
if mode != "nochange":
    name = os.environ.get("STUB_FILE") or ("work-%s.txt" % tid if os.environ.get("STUB_PER_TASK") else "work.txt")
    open(name, "w").write("work for " + tid)
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
        self.cfg_path = self.root / ".tidetable" / "overnight.json"
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

    def two_phases(self, s) -> None:
        """Move task 1.2 into its own phase 2 so a phase closes mid-run."""
        data = json.loads(s.todo.read_text(encoding="utf-8"))
        t2 = data["phases"][0]["tasks"].pop(1)
        data["phases"].append({"id": "2", "title": "P2", "goal": "g2", "tasks": [t2]})
        s.todo.write_text(json.dumps(data), encoding="utf-8")
        _git(s.root, "add", "-A")
        subprocess.run(["git", "-C", str(s.root), "commit", "-q", "-m", "phases"], check=True,
                       capture_output=True)

    def test_phase_review_runs_once_when_the_phase_completes(self):
        s = self.scratch(tasks=2)
        log = s.root.parent / "reviews.log"
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False, "phase": {"enabled": True}}})
        code, out = s.run("done", STUB_LOG=str(log))
        self.assertEqual(code, 0, out)
        self.assertEqual(s.status("1.1"), "done")
        self.assertEqual(s.status("1.2"), "done")
        self.assertEqual(log.read_text().split(), ["phase"])
        self.assertIn("PHASE REVIEW PASS 1", out)

    def test_task_and_phase_review_are_independent(self):
        s = self.scratch(tasks=2, review=True)
        log = s.root.parent / "reviews.log"
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": True, "phase": {"enabled": True}}})
        s.run("done", STUB_LOG=str(log))
        self.assertEqual(log.read_text().split(), ["task", "task", "phase"])

    def test_phase_review_off_by_default(self):
        s = self.scratch(tasks=2, review=True)
        log = s.root.parent / "reviews.log"
        s.run("done", STUB_LOG=str(log))
        self.assertEqual(log.read_text().split(), ["task", "task"])

    def test_phase_review_fail_continues_by_default(self):
        s = self.scratch(tasks=2)
        self.two_phases(s)
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False, "phase": {"enabled": True}}})
        code, out = s.run("done", STUB_PHASE_REVIEW="FAIL parts do not fit")
        self.assertIn("PHASE REVIEW FAIL 1: parts do not fit", out)
        self.assertEqual(s.status("1.1"), "done")
        self.assertEqual(s.status("1.2"), "done")

    def test_phase_review_fail_can_stop_the_run(self):
        s = self.scratch(tasks=2)
        self.two_phases(s)
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False,
                                 "phase": {"enabled": True, "on_fail": "stop"}}})
        code, out = s.run("done", STUB_PHASE_REVIEW="FAIL parts do not fit")
        self.assertEqual(code, 1)
        self.assertEqual(s.status("1.1"), "done")
        self.assertEqual(s.status("1.2"), "pending")

    def test_no_phase_review_flag_wins_over_config(self):
        s = self.scratch(tasks=2)
        log = s.root.parent / "reviews.log"
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False, "phase": {"enabled": True}}})
        s.run("done", "--hours", "1", "--no-phase-review", STUB_LOG=str(log))
        self.assertFalse(log.exists())

    def test_phase_review_model_flag_enables_it(self):
        s = self.scratch(tasks=2)
        log = s.root.parent / "reviews.log"
        s.run("done", "--hours", "1", "--phase-review-model", "anthropic/stub", STUB_LOG=str(log))
        self.assertEqual(log.read_text().split(), ["phase"])

    def test_follow_echoes_agent_output_and_still_closes(self):
        s = self.scratch()
        code, out = s.run("done", "--hours", "1", "--follow")
        self.assertEqual(code, 0, out)
        self.assertIn("OVERNIGHT_SUMMARY: add work file", out)
        self.assertEqual(s.status(), "done")

    def test_no_follow_keeps_agent_output_off_the_console(self):
        s = self.scratch()
        code, out = s.run("done", "--hours", "1", "--no-follow")
        self.assertNotIn("OVERNIGHT_SUMMARY", out)
        self.assertEqual(s.status(), "done")

    def parallel_scratch(self, touches: list[list[str]]) -> Scratch:
        s = self.scratch(tasks=len(touches))
        data = json.loads(s.todo.read_text(encoding="utf-8"))
        for t, tt in zip(data["phases"][0]["tasks"], touches):
            if tt:
                t["touches"] = tt
        s.todo.write_text(json.dumps(data), encoding="utf-8")
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False}})
        return s

    @staticmethod
    def max_concurrency(track: Path) -> int:
        events = sorted((float(ts), kind) for kind, _tid, ts in
                        (ln.split() for ln in track.read_text().splitlines()))
        cur = best = 0
        for _ts, kind in events:
            cur += 1 if kind == "s" else -1
            best = max(best, cur)
        return best

    def test_jobs_run_disjoint_tasks_at_once_and_merge_them(self):
        s = self.parallel_scratch([["a/"], ["b/"], ["c/"]])
        track = s.root.parent / "track.log"
        code, out = s.run("done", "--hours", "1", "--jobs", "3", STUB_TRACK=str(track), STUB_PER_TASK="1")
        self.assertEqual(code, 0, out)
        for tid in ("1.1", "1.2", "1.3"):
            self.assertEqual(s.status(tid), "done")
            self.assertTrue((s.work / f"work-{tid}.txt").exists())
        self.assertGreaterEqual(self.max_concurrency(track), 2)
        subjects = _git(s.root, "log", s.branch, "--format=%s")
        for tid in ("1.1", "1.2", "1.3"):
            self.assertIn(f"{tid}: add work file", subjects)
        # job worktrees and branches are removed once merged
        self.assertEqual(len(_git(s.root, "worktree", "list").strip().splitlines()), 2)
        self.assertNotIn("-1.1", _git(s.root, "branch", "--list"))

    def test_jobs_serialise_tasks_with_overlapping_or_missing_touches(self):
        s = self.parallel_scratch([["lib/"], ["lib/sub/"], []])
        track = s.root.parent / "track.log"
        code, out = s.run("done", "--hours", "1", "--jobs", "3", STUB_TRACK=str(track), STUB_PER_TASK="1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.max_concurrency(track), 1)
        for tid in ("1.1", "1.2", "1.3"):
            self.assertEqual(s.status(tid), "done")

    def test_job_merge_conflict_keeps_branch_and_reopens_task(self):
        s = self.parallel_scratch([["a/"], ["b/"]])
        track = s.root.parent / "track.log"
        # both tasks write work.txt with different content: the second merge conflicts
        code, out = s.run("done", "--hours", "1", "--jobs", "2", STUB_TRACK=str(track))
        self.assertIn("MERGE CONFLICT", out)
        statuses = sorted(s.status(t) for t in ("1.1", "1.2"))
        self.assertEqual(statuses, ["done", "pending"])
        self.assertIn(s.branch + "-", _git(s.root, "branch", "--list"))

    def test_jobs_flag_defaults_to_config(self):
        s = self.parallel_scratch([["a/"], ["b/"]])
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}],
                      "review": {"enabled": False}, "parallel": {"jobs": 2}})
        track = s.root.parent / "track.log"
        s.run("done", "--hours", "1", STUB_TRACK=str(track), STUB_PER_TASK="1")
        self.assertEqual(self.max_concurrency(track), 2)

    def test_ctrl_c_is_logged_as_interrupted_not_no_more_tasks(self):
        s = self.scratch()
        with mock.patch.object(overnight.Runner, "run_task", side_effect=KeyboardInterrupt):
            code, out = s.run("done", "--hours", "1")
        self.assertEqual(code, 1)
        log = (s.root / ".overnight" / "log.md").read_text(encoding="utf-8")
        self.assertIn("END (interrupted (Ctrl-C))", log)
        self.assertNotIn("END (no more ready tasks)", log)

    def test_runner_credentials_are_not_in_the_agent_environment(self):
        s = self.scratch()
        dump = s.root.parent / "envdump.txt"
        s.run("done", "--hours", "1", STUB_ENVDUMP=str(dump), TIDETABLE_TOKEN="tk-secret",
              KEEP_ME="1")
        self.assertEqual(dump.read_text(), "KEEP_ME")

    def test_agent_env_drop_hides_extra_names(self):
        s = self.scratch()
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}], "review": {"enabled": False},
                      "agent_env_drop": ["KEEP_*"]})
        dump = s.root.parent / "envdump.txt"
        s.run("done", "--hours", "1", STUB_ENVDUMP=str(dump), KEEP_ME="1")
        self.assertEqual(dump.read_text(), "")

    def test_prompt_forbids_printing_secrets_and_env_dumps_are_denied(self):
        s = self.scratch()
        code, out = s.run("done", "--hours", "1", "--dry-run")
        self.assertIn("Never print, echo, cat or otherwise output environment variables", out)
        self.assertIn("printenv*", "".join(overnight.BASE_SHELL_DENY))
        self.assertIn("env", overnight.BASE_SHELL_DENY)

    def test_custom_command_can_opt_into_the_runners_shell_permissions(self):
        s = self.scratch()
        s.set_config({"gate": [{"cmd": [sys.executable, "-c", "pass"]}], "review": {"enabled": False},
                      "agent_commands": {"viaoc": {"cmd": ["echo", "{prompt}"],
                                                   "opencode_permissions": True}}})
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["overnight", "run", str(s.todo), "--model", "viaoc/m", "--dry-run"])
        self.assertIn('"permission"', buf.getvalue())
        self.assertIn('"env": "deny"', buf.getvalue())

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

    def test_symlinked_root_still_resolves_the_todo_path(self):
        # A root that is not already canonical (a symlink, or macOS /var under
        # /private/var) must not break todo_rel: __post_init__ resolves the todo
        # path, so it must resolve home the same way or relative_to() raises.
        s = self.scratch()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        link = Path(tmp.name) / "link"
        link.symlink_to(s.root)
        c = json.loads(json.dumps(overnight.DEFAULTS))
        r = overnight.Runner(todo=link / s.todo.name, root=link, cfg=c,
                             date="2026-01-01", deadline=datetime.now())
        self.assertEqual(r.home, s.root.resolve())
        self.assertEqual(r.todo_rel, s.todo.name)


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

            def fake(argv, cwd, stdin=None, timeout=0, env=None, follow=False, prefix="", drop=None):
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
            (root / ".tidetable").mkdir()
            (root / ".tidetable" / "overnight.json").write_text('{"gates": []}')
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
        self.assertIn("tidetable\nstart/done/reset/status".replace("\n", " "), p)
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


class HubModeTests(unittest.TestCase):
    """`--server`: the todo lives on a hub, the repo and worktree stay local."""

    TOKEN = "hub-test-token"

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer

        from tidetable import serve

        self.s = Scratch()
        self.addCleanup(self.s.close)
        hub_dir = Path(self.s._tmp.name) / "hub"
        hub_dir.mkdir()
        self.hub_todo = hub_dir / "todo.json"
        self.hub_todo.write_text(self.s.todo.read_text(encoding="utf-8"), encoding="utf-8")
        registry = hub_dir / "registry.json"
        registry.write_text(json.dumps({"projects": [
            {"slug": "hub", "name": "Hub", "repo_url": "", "todo_path": str(self.hub_todo),
             "default_branch": "main"}]}), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), serve._Handler)
        server.daemon_threads = True
        server.registry = str(registry)
        server.sessions_db = str(hub_dir / "sessions.db")
        server.tokens_db = str(hub_dir / "tokens.db")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def run_hub(self, mode: str = "done", *extra: str, token: str | None = None) -> tuple[int, str]:
        env = {**self.s.env, "STUB_MODE": mode, "STUB_REVIEW": "PASS", "TK_API_TOKEN": self.TOKEN}
        argv = ["--server", self.url, "--token", self.TOKEN if token is None else token,
                "overnight", "run", "hub", "--repo", str(self.s.root),
                "--model", "anthropic/stub", *(extra or ("--hours", "1"))]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def hub_status(self, tid: str = "1.1") -> str:
        data = json.loads(self.hub_todo.read_text(encoding="utf-8"))
        return next(t["status"] for p in data["phases"] for t in p["tasks"] if t["id"] == tid)

    def test_done_closes_the_task_on_the_hub_and_commits_code_locally(self):
        code, out = self.run_hub("done")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hub_status(), "done")
        # the repo's own todo.json is not the todo; nothing of it was committed or changed
        self.assertEqual(json.loads(self.s.todo.read_text(encoding="utf-8"))
                         ["phases"][0]["tasks"][0]["status"], "pending")
        self.assertIn("1.1: add work file", _git(self.s.root, "log", self.s.branch, "--format=%s"))
        self.assertTrue((self.s.work / "work.txt").exists())
        self.assertEqual(_git(self.s.work, "status", "--porcelain").strip(), "")
        self.assertNotIn("todo.json", _git(self.s.root, "log", f"main..{self.s.branch}",
                                           "--name-only", "--format="))

    def test_owner_names_the_machine_so_two_runners_never_share_an_identity(self):
        import socket

        self.run_hub("done")
        events = (Path(str(self.hub_todo) + ".events.jsonl")).read_text(encoding="utf-8")
        owners = {json.loads(l).get("owner") for l in events.splitlines() if l.strip()}
        self.assertIn(f"overnight-{datetime.now():%Y-%m-%d}@{socket.gethostname()}", owners)

    def test_gate_failure_returns_the_hub_task_to_pending(self):
        s = Scratch(gate_exit=1)
        self.addCleanup(s.close)
        self.s = s
        self.setUp_hub_for(s)
        code, out = self.run_hub("done")
        self.assertEqual(self.hub_status(), "pending")
        self.assertIn("gate", out)
        # a failed attempt on a hub must not commit "everything" as todo changes
        self.assertNotIn("chore(overnight)", _git(s.root, "log", s.branch, "--format=%s"))

    def setUp_hub_for(self, scratch: "Scratch") -> None:
        self.hub_todo.write_text(scratch.todo.read_text(encoding="utf-8"), encoding="utf-8")

    def test_unreachable_hub_or_bad_token_aborts_before_any_work(self):
        code, out = self.run_hub("done", token="wrong")
        self.assertEqual(code, 1)
        self.assertIn("not reachable", out)
        self.assertFalse((self.s.work / "work.txt").exists())
        self.assertEqual(self.hub_status(), "pending")

    def test_local_flag_keeps_the_runner_off_a_server_in_the_environment(self):
        """--local wins over $TIDETABLE_SERVER, and the runner's CLI calls inherit that."""
        env = {**self.s.env, "STUB_MODE": "done", "STUB_REVIEW": "PASS",
               "TIDETABLE_SERVER": "http://127.0.0.1:1", "TIDETABLE_TOKEN": "x"}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(buf):
            code = cli.main(["--local", "overnight", "run", str(self.s.todo),
                             "--model", "anthropic/stub", "--hours", "1"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertEqual(self.s.status(), "done")
