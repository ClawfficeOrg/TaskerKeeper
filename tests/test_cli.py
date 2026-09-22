"""Tests for the TaskerKeeper CLI.

Stdlib unittest on purpose: `python -m unittest` works in a bare checkout with
no dev dependencies. pytest runs these too.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from taskerkeeper import agents, cli, jsonio, serve, sessions

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"


def todo(*phases: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "project": {
            "name": "Test",
            "version": {"milestone": "v1", "release_version": "0.1.0"},
            "description": "fixture",
        },
        "phases": list(phases),
    }


def phase(pid: str, *tasks: dict, prerequisites: list[str] | None = None, **extra) -> dict:
    out = {"id": pid, "title": f"Phase {pid}", "goal": "goal", "tasks": list(tasks)}
    if prerequisites:
        out["prerequisites"] = prerequisites
    out.update(extra)
    return out


def task(tid: str, status: str = "pending", prerequisites: list[str] | None = None, **extra) -> dict:
    out = {"id": tid, "title": f"Task {tid}", "status": status}
    if prerequisites:
        out["prerequisites"] = prerequisites
    out.update(extra)
    return out


class TempTodo:
    """A todo file on disk that the CLI can mutate."""

    def __init__(self, data: dict):
        self.data = data
        self._tmp = TemporaryDirectory()

    def __enter__(self) -> str:
        self.path = Path(self._tmp.name) / "todo.json"
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        return str(self.path)

    def __exit__(self, *exc):
        self._tmp.cleanup()


def run(*argv: str) -> tuple[int, str]:
    """Invoke the CLI, capturing exit code and stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


def reload(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class SchemaPackagingTest(unittest.TestCase):
    def test_schema_is_package_data(self):
        # The bug this guards: SCHEMA_PATH pointed outside the package, so any
        # non-editable install could not validate anything.
        self.assertTrue(cli.SCHEMA_PATH.is_file(), cli.SCHEMA_PATH)
        self.assertEqual(cli.SCHEMA_PATH.parent.parent.name, "taskerkeeper")

    def test_examples_validate(self):
        for example in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(example=example.name):
                code, _ = run("validate", str(example))
                self.assertEqual(code, 0)


class EligibilityTest(unittest.TestCase):
    def test_task_prerequisites_gate(self):
        data = todo(phase("1.0", task("1.0.1", "done"), task("1.0.2", prerequisites=["1.0.1"]),
                          task("1.0.3", prerequisites=["1.0.2"])))
        self.assertEqual([t["id"] for t in cli.ready_tasks(data)], ["1.0.2"])

    def test_phase_prerequisites_gate(self):
        # 1.1.1 has no task prereqs, but its phase requires 1.0, which is not done.
        data = todo(
            phase("1.0", task("1.0.1", "pending")),
            phase("1.1", task("1.1.1"), prerequisites=["1.0"]),
        )
        self.assertEqual([t["id"] for t in cli.ready_tasks(data)], ["1.0.1"])

    def test_phase_completes_when_all_tasks_terminal(self):
        data = todo(
            phase("1.0", task("1.0.1", "done"), task("1.0.2", "cancelled")),
            phase("1.1", task("1.1.1"), prerequisites=["1.0"]),
        )
        self.assertEqual([t["id"] for t in cli.ready_tasks(data)], ["1.1.1"])

    def test_ready_orders_numerically(self):
        data = todo(phase("1.0", task("1.0.10"), task("1.0.9"), task("1.0.2")))
        self.assertEqual([t["id"] for t in cli.ready_tasks(data)], ["1.0.2", "1.0.9", "1.0.10"])

    def test_next_resumes_in_progress(self):
        data = todo(phase("1.0", task("1.0.1", "in_progress"), task("1.0.2")))
        self.assertEqual(cli.find_next(data)["id"], "1.0.1")
        self.assertEqual(cli.find_next(data, resume=False)["id"], "1.0.2")

    def test_blocked_report_names_the_blocker(self):
        data = todo(
            phase("1.0", task("1.0.1")),
            phase("1.1", task("1.1.1", prerequisites=["1.0.1"]), prerequisites=["1.0"]),
        )
        blocked = {b["id"]: b["blocked_by"] for b in cli.blocked_report(data)}
        self.assertEqual(blocked["1.1.1"], ["task 1.0.1", "phase 1.0"])


class ValidationTest(unittest.TestCase):
    def errors(self, data: dict) -> list[str]:
        return cli.semantic_errors(data)[0]

    def warnings(self, data: dict) -> list[str]:
        return cli.semantic_errors(data)[1]

    def test_dangling_prerequisite(self):
        data = todo(phase("1.0", task("1.0.1", prerequisites=["9.9.9"])))
        self.assertIn("task 1.0.1 requires unknown task 9.9.9", self.errors(data))

    def test_dangling_phase_prerequisite(self):
        data = todo(phase("1.0", task("1.0.1"), prerequisites=["0.9"]))
        self.assertIn("phase 1.0 requires unknown phase 0.9", self.errors(data))

    def test_duplicate_task_id(self):
        data = todo(phase("1.0", task("1.0.1"), task("1.0.1")))
        self.assertIn("duplicate task id: 1.0.1", self.errors(data))

    def test_task_id_must_match_its_phase(self):
        data = todo(phase("1.0", task("1.0.1")), phase("1.1", task("1.0.2")))
        self.assertTrue(any("implies phase 1.0" in e for e in self.errors(data)))

    def test_cycle_detected(self):
        data = todo(phase("1.0",
                          task("1.0.1", prerequisites=["1.0.3"]),
                          task("1.0.2", prerequisites=["1.0.1"]),
                          task("1.0.3", prerequisites=["1.0.2"])))
        self.assertTrue(any(e.startswith("prerequisite cycle") for e in self.errors(data)))

    def test_self_prerequisite(self):
        data = todo(phase("1.0", task("1.0.1", prerequisites=["1.0.1"])))
        self.assertIn("task 1.0.1 requires itself", self.errors(data))

    def test_moved_requires_target(self):
        data = todo(phase("1.0", task("1.0.1", "moved")))
        self.assertIn("task 1.0.1 is moved but has no moved_to", self.errors(data))

    def test_prerequisite_on_cancelled_task_warns(self):
        data = todo(phase("1.0", task("1.0.1", "cancelled"), task("1.0.2", prerequisites=["1.0.1"])))
        self.assertEqual(self.errors(data), [])
        self.assertTrue(any("blocked forever" in w for w in self.warnings(data)))

    def test_valid_file_is_clean(self):
        data = todo(phase("1.0", task("1.0.1", "done"), task("1.0.2", prerequisites=["1.0.1"])))
        self.assertEqual(cli.semantic_errors(data), ([], []))


class MutationTest(unittest.TestCase):
    def test_start_claims_task(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, _ = run("start", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertEqual(reload(path)["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_start_refuses_blocked_task(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2", prerequisites=["1.0.1"])))) as path:
            code, out = run("start", path, "1.0.2")
            self.assertEqual(code, 1)
            self.assertIn("blocked by", out)
            self.assertEqual(reload(path)["phases"][0]["tasks"][1]["status"], "pending")

    def test_start_force_overrides(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2", prerequisites=["1.0.1"])))) as path:
            code, _ = run("start", path, "1.0.2", "--force")
            self.assertEqual(code, 0)
            self.assertEqual(reload(path)["phases"][0]["tasks"][1]["status"], "in_progress")

    def test_reset_unsticks_orphaned_in_progress(self):
        with TempTodo(todo(phase("1.0", task("1.0.1", "in_progress")))) as path:
            code, _ = run("reset", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertEqual(reload(path)["phases"][0]["tasks"][0]["status"], "pending")

    def test_done_refuses_unmet_prerequisites(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2", prerequisites=["1.0.1"])))) as path:
            code, out = run("done", path, "1.0.2")
            self.assertEqual(code, 1)
            self.assertIn("unmet prerequisites", out)
            self.assertEqual(reload(path)["phases"][0]["tasks"][1]["status"], "pending")

    def test_done_reports_unblocked(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2", prerequisites=["1.0.1"])))) as path:
            code, out = run("done", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertIn("1.0.2", out)

    def test_done_collects_changelog_entry(self):
        data = todo(phase("1.0", task("1.0.1"),
                          release={"version": "0.1.0", "tag_on_complete": True}))
        with TempTodo(data) as path:
            run("done", path, "1.0.1", "--changelog", "Added the thing")
            entries = reload(path)["phases"][0]["release"]["changelog_entries"]
            self.assertEqual(entries, ["Added the thing"])

    def test_changelog_files_under_last_releasing_phase(self):
        data = todo(
            phase("1.0", task("1.0.1")),
            phase("1.1", task("1.1.1"), release={"version": "0.1.0"}),
        )
        with TempTodo(data) as path:
            run("done", path, "1.0.1", "--changelog", "Groundwork")
            entries = reload(path)["phases"][1]["release"]["changelog_entries"]
            self.assertEqual(entries, ["Groundwork"])

    def test_status_moved_requires_existing_target(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            self.assertEqual(run("status", path, "1.0.1", "moved", "--moved-to", "9.9.9")[0], 1)
            self.assertEqual(run("status", path, "1.0.1", "moved", "--moved-to", "1.0.2")[0], 0)
            self.assertEqual(reload(path)["phases"][0]["tasks"][0]["moved_to"], "1.0.2")

    def test_timestamps_use_z_suffix(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("done", path, "1.0.1")
            self.assertTrue(reload(path)["phases"][0]["tasks"][0]["updated_at"].endswith("Z"))


class AddTest(unittest.TestCase):
    def test_add_accepts_full_task_shape(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, _ = run("add", path, "--phase", "1.0", "--title", "New",
                          "--goal", "do it", "--prereq", "1.0.1",
                          "--complexity", "High", "--agent", "pro_dev_agent",
                          "--parallel-group", "grp", "--touches", "src/a.py",
                          "--success", "it works")
            self.assertEqual(code, 0)
            new = reload(path)["phases"][0]["tasks"][-1]
            self.assertEqual(new["id"], "1.0.2")
            self.assertEqual(new["prerequisites"], ["1.0.1"])
            self.assertEqual(new["complexity"], "High")
            self.assertEqual(new["parallel_group"], "grp")
            self.assertEqual(new["touches"], ["src/a.py"])

    def test_add_rejects_unknown_prerequisite(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, out = run("add", path, "--phase", "1.0", "--title", "New", "--prereq", "9.9.9")
            self.assertEqual(code, 1)
            self.assertIn("does not exist", out)

    def test_add_survives_non_numeric_ids(self):
        # int(id.split(".")[-1]) used to crash the whole command here.
        with TempTodo(todo(phase("1.0", task("1.0.a")))) as path:
            code, _ = run("add", path, "--phase", "1.0", "--title", "New")
            self.assertEqual(code, 0)
            self.assertEqual(reload(path)["phases"][0]["tasks"][-1]["id"], "1.0.1")


class OutputTest(unittest.TestCase):
    def test_next_json_is_parseable(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, out = run("next", path, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["task"]["id"], "1.0.1")

    def test_ready_json_lists_every_runnable_task(self):
        data = todo(phase("1.0", task("1.0.1"), task("1.0.2"), task("1.0.3", prerequisites=["1.0.1"])))
        with TempTodo(data) as path:
            _, out = run("ready", path, "--json")
            self.assertEqual([t["id"] for t in json.loads(out)["ready"]], ["1.0.1", "1.0.2"])

    def test_ready_exits_nonzero_when_nothing_to_do(self):
        with TempTodo(todo(phase("1.0", task("1.0.1", "done")))) as path:
            self.assertEqual(run("ready", path, "--json")[0], 1)

    def test_list_and_deps_json(self):
        with TempTodo(todo(phase("1.0", task("1.0.1", "done"), task("1.0.2", prerequisites=["1.0.1"])))) as path:
            _, out = run("list", path, "--json")
            self.assertEqual(json.loads(out)["summary"]["done"], 1)
            _, out = run("deps", path, "1.0.2", "--json")
            self.assertEqual(json.loads(out)["prerequisites"][0]["status"], "done")

    def test_convert_renders_markdown(self):
        with TempTodo(todo(phase("1.0", task("1.0.1", "done")))) as path:
            code, out = run("convert", path)
            self.assertEqual(code, 0)
            self.assertIn("## Phase 1.0", out)
            self.assertIn("- [x] **1.0.1**", out)


class AgentConfigTest(unittest.TestCase):
    """Tier -> provider/model resolution across the config layers."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.user_dir = self.root / "userconf"
        self.repo_dir = self.root / "repo"
        (self.repo_dir / ".git").mkdir(parents=True)
        # Keep the real home directory and any real repo config out of the test.
        patch = mock.patch.dict(os.environ, {"TASKERKEEPER_CONFIG_HOME": str(self.user_dir)})
        patch.start()
        self.addCleanup(patch.stop)

    def write_todo(self, data: dict) -> str:
        path = self.repo_dir / "todo.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return str(path)

    def test_defaults_cover_every_tier_the_schema_allows(self):
        schema = json.loads(cli.SCHEMA_PATH.read_text(encoding="utf-8"))
        allowed = set(schema["$defs"]["task"]["properties"]["agent"]["enum"])
        self.assertEqual(set(agents.DEFAULT_TIERS), allowed)
        for tier, config in agents.DEFAULT_TIERS.items():
            with self.subTest(tier=tier):
                self.assertTrue(config["provider"] and config["model"])

    def test_user_config_overrides_builtin(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "set", "mid_dev_agent", "--model", "claude-opus-5")
        tiers, sources = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["mid_dev_agent"]["model"], "claude-opus-5")
        self.assertEqual(sources["mid_dev_agent"]["model"], agents.LAYER_USER)
        # provider was not set at the user layer, so it still comes from defaults
        self.assertEqual(sources["mid_dev_agent"]["provider"], agents.LAYER_BUILTIN)

    def test_repo_config_overrides_user(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "set", "mid_dev_agent", "--model", "user-model")
        run("agents", "set", "mid_dev_agent", "--model", "repo-model",
            "--scope", "repo", "--todo", path)
        tiers, sources = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["mid_dev_agent"]["model"], "repo-model")
        self.assertEqual(sources["mid_dev_agent"]["model"], agents.LAYER_REPO)

    def test_todo_config_overrides_repo(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "set", "mid_dev_agent", "--model", "repo-model",
            "--scope", "repo", "--todo", path)
        run("agents", "set", "mid_dev_agent", "--model", "todo-model",
            "--scope", "todo", "--todo", path)
        tiers, sources = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["mid_dev_agent"]["model"], "todo-model")
        self.assertEqual(sources["mid_dev_agent"]["model"], agents.LAYER_TODO)

    def test_task_override_beats_every_layer(self):
        tiers = {"mid_dev_agent": {"provider": "anthropic", "model": "claude-sonnet-5"}}
        pinned = task("1.0.1", agent="mid_dev_agent", model="claude-opus-5")
        self.assertEqual(agents.resolve_task(pinned, tiers)["model"], "claude-opus-5")
        # and the provider it did not override still comes from the tier
        self.assertEqual(agents.resolve_task(pinned, tiers)["provider"], "anthropic")

    def test_unknown_tier_resolves_to_no_model(self):
        # The tier is still reported back — a supervisor needs to know which
        # tier it failed to resolve — but nothing is invented for it.
        resolved = agents.resolve_task(task("1.0.1", agent="nope"), {})
        self.assertEqual(resolved, {"agent": "nope", "agent_derived": False})

    def test_arbitrary_settings_pass_through(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="pro_dev_agent"))))
        run("agents", "set", "pro_dev_agent", "--option", "effort=xhigh")
        tiers, _ = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["pro_dev_agent"]["effort"], "xhigh")

    def test_set_rejects_malformed_option(self):
        code, out = run("agents", "set", "pro_dev_agent", "--option", "effort")
        self.assertEqual(code, 1)
        self.assertIn("key=value", out)

    def test_set_with_no_settings_is_an_error(self):
        self.assertEqual(run("agents", "set", "pro_dev_agent")[0], 1)

    def test_unset_removes_a_key_then_the_tier(self):
        run("agents", "set", "mid_dev_agent", "--model", "m", "--provider", "p")
        run("agents", "unset", "mid_dev_agent", "--key", "model")
        stored = agents.read_tiers(agents.user_config_path())
        self.assertEqual(stored["mid_dev_agent"], {"provider": "p"})
        run("agents", "unset", "mid_dev_agent")
        self.assertNotIn("mid_dev_agent", agents.read_tiers(agents.user_config_path()))

    def test_unset_reports_when_there_is_nothing_to_remove(self):
        self.assertEqual(run("agents", "unset", "mid_dev_agent")[0], 1)

    def test_todo_scope_needs_a_todo_file(self):
        code, out = run("agents", "set", "mid_dev_agent", "--model", "m", "--scope", "todo")
        self.assertEqual(code, 1)
        self.assertIn("needs a todo file", out)

    def test_missing_config_file_is_not_an_error(self):
        self.assertEqual(agents.read_tiers(self.root / "nope" / "agents.json"), {})

    def test_ready_json_carries_the_resolved_model(self):
        path = self.write_todo(todo(phase("1.0",
                                          task("1.0.1", agent="mid_dev_agent"),
                                          task("1.0.2", agent="pro_dev_agent",
                                               model="pinned-model"))))
        run("agents", "set", "mid_dev_agent", "--model", "tier-model")
        _, out = run("ready", path, "--json")
        resolved = {t["id"]: t["model"] for t in json.loads(out)["ready"]}
        self.assertEqual(resolved["1.0.1"], "tier-model")
        self.assertEqual(resolved["1.0.2"], "pinned-model")

    def test_next_json_carries_the_resolved_model(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="basic_dev_agent"))))
        _, out = run("next", path, "--json")
        self.assertEqual(json.loads(out)["task"]["provider"], "anthropic")
        self.assertEqual(json.loads(out)["task"]["model"],
                         agents.DEFAULT_TIERS["basic_dev_agent"]["model"])

    def test_show_json_reports_sources_and_overrides(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent",
                                                      model="pinned"))))
        _, out = run("agents", "show", "--todo", path, "--json")
        payload = json.loads(out)
        self.assertEqual(payload["tiers"]["mid_dev_agent"]["_sources"]["model"],
                         agents.LAYER_BUILTIN)
        self.assertEqual(payload["overrides"], [{"id": "1.0.1", "model": "pinned"}])

    def test_path_prints_the_scope_file(self):
        _, out = run("agents", "path", "--scope", "user")
        self.assertEqual(out.strip(), str(agents.user_config_path()))

    def test_repo_path_lands_at_the_git_root(self):
        nested = self.repo_dir / "docs" / "sub"
        nested.mkdir(parents=True)
        found = agents.repo_config_path(nested / "todo.json")
        self.assertEqual(found, self.repo_dir / ".taskerkeeper" / "agents.json")

    def test_every_preset_covers_every_tier(self):
        schema = json.loads(cli.SCHEMA_PATH.read_text(encoding="utf-8"))
        allowed = set(schema["$defs"]["task"]["properties"]["agent"]["enum"])
        for name, preset in agents.PROVIDER_PRESETS.items():
            with self.subTest(provider=name):
                self.assertEqual(set(preset), allowed)
                for tier, config in preset.items():
                    self.assertEqual(config["provider"], name, tier)
                    self.assertTrue(config["model"], tier)

    def test_use_switches_every_tier_at_once(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="pro_dev_agent"))))
        run("agents", "use", "opencode-go")
        tiers, sources = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["pro_dev_agent"], {"provider": "opencode-go",
                                                  "model": "deepseek-v4-pro"})
        self.assertEqual(tiers["flagship"]["model"], "qwen3.8-max")
        self.assertEqual(tiers["basic_dev_agent"]["model"], "glm-5.3-flash")
        self.assertEqual(tiers["mid_dev_agent"]["model"], "glm-5.3-flash")
        self.assertEqual(sources["pro_dev_agent"]["model"], f"{agents.LAYER_USER} preset")

    def test_hand_set_tier_beats_the_preset_in_the_same_scope(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "use", "opencode-go")
        run("agents", "set", "mid_dev_agent", "--model", "glm-5.3-pro")
        tiers, sources = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["mid_dev_agent"]["model"], "glm-5.3-pro")
        self.assertEqual(sources["mid_dev_agent"]["model"], agents.LAYER_USER)
        # the tier it did not touch still comes from the preset
        self.assertEqual(tiers["flagship"]["model"], "qwen3.8-max")

    def test_repo_preset_beats_a_user_tier(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="flagship"))))
        run("agents", "set", "flagship", "--model", "user-model")
        run("agents", "use", "opencode-go", "--scope", "repo", "--todo", path)
        tiers, _ = agents.resolve_tiers(json.loads(Path(path).read_text()), path)
        self.assertEqual(tiers["flagship"]["model"], "qwen3.8-max")

    def test_todo_preset_beats_the_repo_preset(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="flagship"))))
        run("agents", "use", "opencode-go", "--scope", "repo", "--todo", path)
        run("agents", "use", "anthropic", "--scope", "todo", "--todo", path)
        data = json.loads(Path(path).read_text())
        self.assertEqual(agents.active_provider(data, path), ("anthropic", agents.LAYER_TODO))
        tiers, _ = agents.resolve_tiers(data, path)
        self.assertEqual(tiers["flagship"]["model"],
                         agents.PROVIDER_PRESETS["anthropic"]["flagship"]["model"])

    def test_default_provider_when_nothing_selects_one(self):
        self.assertEqual(agents.active_provider(), (agents.DEFAULT_PROVIDER,
                                                    agents.LAYER_BUILTIN))

    def test_use_rejects_an_unknown_preset(self):
        code, out = run("agents", "use", "not-a-provider")
        self.assertEqual(code, 1)
        self.assertIn("opencode-go", out)
        self.assertEqual(agents.read_config(agents.user_config_path()), {})

    def test_use_needs_a_provider_or_clear(self):
        self.assertEqual(run("agents", "use")[0], 1)

    def test_clear_restores_the_default_preset(self):
        run("agents", "use", "opencode-go")
        code, _ = run("agents", "use", "--clear")
        self.assertEqual(code, 0)
        self.assertEqual(agents.active_provider()[0], agents.DEFAULT_PROVIDER)
        # clearing twice reports that there was nothing to clear
        self.assertEqual(run("agents", "use", "--clear")[0], 1)

    def test_providers_json_lists_presets_and_the_active_one(self):
        run("agents", "use", "opencode-go")
        _, out = run("agents", "providers", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["active"], "opencode-go")
        self.assertEqual(set(payload["presets"]), set(agents.PROVIDER_PRESETS))

    def test_ready_json_reflects_the_active_preset(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="flagship"))))
        run("agents", "use", "opencode-go")
        _, out = run("ready", path, "--json")
        entry = json.loads(out)["ready"][0]
        self.assertEqual((entry["provider"], entry["model"]), ("opencode-go", "qwen3.8-max"))

    def test_show_json_reports_the_active_preset(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "use", "opencode-go", "--scope", "repo", "--todo", path)
        _, out = run("agents", "show", "--todo", path, "--json")
        payload = json.loads(out)
        self.assertEqual(payload["provider"], "opencode-go")
        self.assertEqual(payload["provider_source"], agents.LAYER_REPO)

    def test_configured_todo_still_validates(self):
        path = self.write_todo(todo(phase("1.0", task("1.0.1", agent="mid_dev_agent"))))
        run("agents", "set", "mid_dev_agent", "--model", "claude-opus-5",
            "--scope", "todo", "--todo", path)
        run("agents", "use", "opencode-go", "--scope", "todo", "--todo", path)
        self.assertEqual(run("validate", path)[0], 0)


class ConcurrencyTest(unittest.TestCase):
    def test_write_is_atomic_and_leaves_no_temp_files(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("done", path, "1.0.1")
            # The event log is a deliberate sibling; a *.tmp is a failed write.
            expected = {"todo.json", "todo.json.events.jsonl"}
            leftovers = [p.name for p in Path(path).parent.iterdir() if p.name not in expected]
            self.assertEqual(leftovers, [])

    def test_lock_blocks_a_second_writer(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            with cli.FileLock(path):
                with self.assertRaises(cli.LockTimeout):
                    with cli.FileLock(path, timeout=0.1):
                        pass
            # Released on exit, so the next writer succeeds.
            self.assertEqual(run("start", path, "1.0.1")[0], 0)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Claims and leases
# ---------------------------------------------------------------------------


class ClaimTest(unittest.TestCase):
    """`start` records who holds a task, so two agents cannot work one task."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"TASKERKEEPER_OWNER": "agent-a"})
        self._env.start()
        self.addCleanup(self._env.stop)

    @staticmethod
    def as_owner(name: str):
        return mock.patch.dict(os.environ, {"TASKERKEEPER_OWNER": name})

    def test_start_records_owner_and_lease(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, _ = run("start", path, "1.0.1")
            self.assertEqual(code, 0)
            claimed = reload(path)["phases"][0]["tasks"][0]
            self.assertEqual(claimed["claimed_by"], "agent-a")
            self.assertIn("claimed_at", claimed)
            self.assertTrue(claimed["lease_expires_at"].endswith("Z"))

    def test_second_agent_cannot_take_a_live_claim(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("start", path, "1.0.1")
            with self.as_owner("agent-b"):
                code, out = run("start", path, "1.0.1")
            self.assertEqual(code, 1)
            self.assertIn("held by agent-a", out)
            self.assertEqual(reload(path)["phases"][0]["tasks"][0]["claimed_by"], "agent-a")

    def test_owner_restarting_refreshes_its_own_lease(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("start", path, "1.0.1")
            code, out = run("start", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertIn("lease refreshed", out)

    def test_expired_lease_can_be_reclaimed(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("start", path, "1.0.1")
            expire(path, "1.0.1")
            with self.as_owner("agent-b"):
                code, out = run("start", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertIn("Reclaimed", out)
            self.assertEqual(reload(path)["phases"][0]["tasks"][0]["claimed_by"], "agent-b")

    def test_next_does_not_hand_out_another_agents_live_task(self):
        # The whole point of claiming: a second agent must get different work.
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            run("start", path, "1.0.1")
            with self.as_owner("agent-b"):
                code, out = run("next", path, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["task"]["id"], "1.0.2")

    def test_next_resumes_the_callers_own_claim(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            run("start", path, "1.0.1")
            code, out = run("next", path, "--json")
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["task"]["id"], "1.0.1")
            self.assertTrue(payload["resumed"])

    def test_next_resumes_an_expired_claim_from_anyone(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            run("start", path, "1.0.1")
            expire(path, "1.0.1")
            with self.as_owner("agent-b"):
                code, out = run("next", path, "--json")
            self.assertEqual(json.loads(out)["task"]["id"], "1.0.1")

    def test_done_refuses_someone_elses_live_claim_without_force(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("start", path, "1.0.1")
            with self.as_owner("agent-b"):
                code, out = run("done", path, "1.0.1")
                self.assertEqual(code, 1)
                self.assertIn("held by agent-a", out)
                self.assertEqual(run("done", path, "1.0.1", "--force")[0], 0)

    def test_terminal_status_clears_the_claim(self):
        for command in (("done",), ("reset",), ("status", "cancelled")):
            with self.subTest(command=command[0]):
                with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
                    run("start", path, "1.0.1")
                    run(command[0], path, "1.0.1", *command[1:])
                    finished = reload(path)["phases"][0]["tasks"][0]
                    for field in cli.CLAIM_FIELDS:
                        self.assertNotIn(field, finished)

    def test_a_claim_with_no_lease_field_is_treated_as_expired(self):
        # Files written by a pre-lease `start` must not deadlock forever.
        legacy = task("1.0.1", status="in_progress", claimed_by="ghost")
        with TempTodo(todo(phase("1.0", legacy))) as path:
            with self.as_owner("agent-b"):
                self.assertEqual(run("start", path, "1.0.1")[0], 0)


def expire(path: str, task_id: str) -> None:
    """Push a task's lease into the past, standing in for a crashed agent."""
    data = reload(path)
    for phase_data in data["phases"]:
        for item in phase_data["tasks"]:
            if item["id"] == task_id:
                item["lease_expires_at"] = "2020-01-01T00:00:00Z"
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Owned-path conflicts
# ---------------------------------------------------------------------------


class DisjointTest(unittest.TestCase):
    def test_paths_conflict_covers_directories(self):
        self.assertTrue(cli.paths_conflict("src/auth.rs", "src/auth.rs"))
        self.assertTrue(cli.paths_conflict("./src/auth.rs", "src/auth.rs"))
        self.assertTrue(cli.paths_conflict("src\\auth.rs", "src/auth.rs"))
        self.assertTrue(cli.paths_conflict("src/", "src/auth.rs"))
        self.assertTrue(cli.paths_conflict("src/routes/handlers.rs", "src/routes"))
        self.assertFalse(cli.paths_conflict("src/auth.rs", "src/authz.rs"))
        self.assertFalse(cli.paths_conflict("src/auth.rs", "docs/auth.rs"))
        self.assertFalse(cli.paths_conflict("", "src/auth.rs"))

    def test_ready_warns_when_owned_paths_overlap(self):
        with TempTodo(todo(phase(
            "1.0",
            task("1.0.1", touches=["src/auth.rs"]),
            task("1.0.2", touches=["src/auth.rs"]),
        ))) as path:
            code, out = run("ready", path)
            self.assertEqual(code, 0)
            self.assertIn("Owned paths overlap", out)
            self.assertIn("1.0.1 ~ 1.0.2", out)

    def test_disjoint_keeps_the_lowest_id_and_defers_the_rest(self):
        with TempTodo(todo(phase(
            "1.0",
            task("1.0.1", touches=["src/routes/"]),
            task("1.0.2", touches=["src/routes/handlers.rs"]),
            task("1.0.3", touches=["docs/readme.md"]),
        ))) as path:
            code, out = run("ready", path, "--disjoint", "--json")
            payload = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual([t["id"] for t in payload["ready"]], ["1.0.1", "1.0.3"])
            self.assertEqual(payload["deferred"][0]["id"], "1.0.2")
            self.assertEqual(payload["deferred"][0]["conflicts_with"], ["1.0.1"])

    def test_in_progress_tasks_hold_their_paths(self):
        with TempTodo(todo(phase(
            "1.0",
            task("1.0.1", status="in_progress", touches=["src/auth.rs"]),
            task("1.0.2", touches=["src/auth.rs"]),
            task("1.0.3", touches=["docs/readme.md"]),
        ))) as path:
            payload = json.loads(run("ready", path, "--disjoint", "--json")[1])
            self.assertEqual([t["id"] for t in payload["ready"]], ["1.0.3"])

    def test_next_disjoint_skips_work_colliding_with_in_progress(self):
        with TempTodo(todo(phase(
            "1.0",
            task("1.0.1", status="in_progress", touches=["src/auth.rs"]),
            task("1.0.2", touches=["src/auth.rs"]),
            task("1.0.3", touches=["docs/readme.md"]),
        ))) as path:
            with mock.patch.dict(os.environ, {"TASKERKEEPER_OWNER": "agent-b"}):
                payload = json.loads(
                    run("next", path, "--disjoint", "--no-resume", "--json")[1])
            self.assertEqual(payload["task"]["id"], "1.0.3")

    def test_tasks_without_touches_never_conflict(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            payload = json.loads(run("ready", path, "--disjoint", "--json")[1])
            self.assertEqual([t["id"] for t in payload["ready"]], ["1.0.1", "1.0.2"])
            self.assertEqual(payload["conflicts"], [])


# ---------------------------------------------------------------------------
# Complexity -> tier
# ---------------------------------------------------------------------------


class ComplexityTierTest(unittest.TestCase):
    def test_range_parsing(self):
        self.assertEqual(agents.parse_complexity_range("Low-High"),
                         ["low", "medium", "high"])
        self.assertEqual(agents.parse_complexity_range("Very High"), ["very high"])
        self.assertEqual(agents.parse_complexity_range("Medium to High"),
                         ["medium", "high"])
        self.assertEqual(agents.parse_complexity_range("low, very high"),
                         ["low", "very high"])
        self.assertEqual(agents.parse_complexity_range(None), [])

    def test_complexity_picks_a_tier_when_no_agent_is_named(self):
        self.assertEqual(agents.effective_agent({"complexity": "Low"}), "basic_dev_agent")
        self.assertEqual(agents.effective_agent({"complexity": "Very High"}), "flagship")

    def test_an_explicit_agent_always_wins(self):
        self.assertEqual(
            agents.effective_agent({"complexity": "Low", "agent": "flagship"}), "flagship")

    def test_no_agent_and_no_complexity_falls_back(self):
        self.assertEqual(agents.effective_agent({}), agents.FALLBACK_TIER)

    def test_configured_complexity_range_overrides_the_default_map(self):
        tiers = {"flagship": {"complexity_range": "Low-High"}}
        self.assertEqual(agents.effective_agent({"complexity": "Low"}, tiers), "flagship")

    def test_resolved_output_reports_a_derived_tier(self):
        resolved = agents.resolve_task({"id": "1.0.1", "complexity": "Low"},
                                       agents.DEFAULT_TIERS)
        self.assertEqual(resolved["agent"], "basic_dev_agent")
        self.assertTrue(resolved["agent_derived"])
        self.assertEqual(resolved["model"], "claude-haiku-4-5")

    def test_add_derives_the_tier_from_complexity(self):
        with TempTodo(todo(phase("1.0"))) as path:
            run("add", path, "--phase", "1.0", "--title", "Hard thing",
                "--complexity", "Very High")
            added = reload(path)["phases"][0]["tasks"][0]
            self.assertEqual(added["agent"], "flagship")


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"TASKERKEEPER_OWNER": "agent-a"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_transitions_are_appended_not_overwritten(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            run("start", path, "1.0.1")
            run("reset", path, "1.0.1")
            run("start", path, "1.0.1")
            run("done", path, "1.0.1", "--changelog", "shipped it")
            events = jsonio.read_events(path)
            self.assertEqual([e["event"] for e in events],
                             ["start", "reset", "start", "done"])
            self.assertEqual(events[-1]["changelog"], "shipped it")
            self.assertTrue(all(e["owner"] == "agent-a" for e in events))

    def test_history_can_filter_to_one_task(self):
        with TempTodo(todo(phase("1.0", task("1.0.1"), task("1.0.2")))) as path:
            run("start", path, "1.0.1")
            run("start", path, "1.0.2")
            payload = json.loads(run("history", path, "--task", "1.0.2", "--json")[1])
            self.assertEqual([e["task"] for e in payload["events"]], ["1.0.2"])

    def test_history_is_empty_before_anything_happens(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            code, out = run("history", path)
            self.assertEqual(code, 1)
            self.assertIn("No events", out)

    def test_the_log_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"TASKERKEEPER_EVENTS": "0"}):
            with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
                run("start", path, "1.0.1")
                self.assertFalse(jsonio.events_path(path).exists())


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def releasing(*tasks: dict) -> dict:
    return phase("1.0", *tasks,
                 release={"version": "v0.1.0", "tag_on_complete": True,
                          "release_notes": "First cut."})


class ReleaseTest(unittest.TestCase):
    def test_done_reports_release_ready_only_when_the_phase_completes(self):
        with TempTodo(todo(releasing(task("1.0.1"), task("1.0.2")))) as path:
            first = json.loads(run("done", path, "1.0.1", "--json")[1])
            self.assertFalse(first["phase_complete"])
            self.assertFalse(first["release_ready"])
            last = json.loads(run("done", path, "1.0.2", "--json")[1])
            self.assertTrue(last["phase_complete"])
            self.assertTrue(last["release_ready"])
            self.assertEqual(last["release"]["version"], "v0.1.0")

    def test_done_never_creates_a_tag_by_itself(self):
        with TempTodo(todo(releasing(task("1.0.1")))) as path:
            with mock.patch("taskerkeeper.cli.subprocess.run") as spawned:
                run("done", path, "1.0.1")
            spawned.assert_not_called()

    def test_release_refuses_an_incomplete_phase(self):
        with TempTodo(todo(releasing(task("1.0.1"), task("1.0.2")))) as path:
            run("done", path, "1.0.1")
            code, out = run("release", path)
            self.assertEqual(code, 1)
            self.assertIn("not complete", out)

    def test_release_reports_the_collected_changelog(self):
        with TempTodo(todo(releasing(task("1.0.1")))) as path:
            run("done", path, "1.0.1", "--changelog", "shipped it")
            payload = json.loads(run("release", path, "--json")[1])
            self.assertEqual(payload["version"], "v0.1.0")
            self.assertTrue(payload["complete"])
            self.assertEqual(payload["changelog_entries"], ["shipped it"])

    def test_tag_message_carries_notes_and_changelog(self):
        state = {"version": "v0.1.0", "release_notes": "First cut.",
                 "changelog_entries": ["a", "b"]}
        self.assertEqual(cli.tag_message(state), "First cut.\n\n- a\n- b\n")

    def test_tag_shells_out_only_with_the_flag(self):
        with TempTodo(todo(releasing(task("1.0.1")))) as path:
            run("done", path, "1.0.1", "--changelog", "shipped it")
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                # First call is the "does this tag exist" probe.
                code = 1 if "rev-parse" in argv else 0
                return subprocess.CompletedProcess(argv, code, "", "")

            with mock.patch("taskerkeeper.cli.subprocess.run", fake_run):
                code, out = run("release", path, "--tag")
            self.assertEqual(code, 0)
            self.assertIn("Tagged v0.1.0", out)
            self.assertIn("tag", calls[-1])
            self.assertIn("v0.1.0", calls[-1])

    def test_tag_refuses_to_clobber_an_existing_tag(self):
        with TempTodo(todo(releasing(task("1.0.1")))) as path:
            run("done", path, "1.0.1")
            with mock.patch("taskerkeeper.cli.subprocess.run",
                            return_value=subprocess.CompletedProcess([], 0, "abc123", "")):
                code, out = run("release", path, "--tag")
            self.assertEqual(code, 1)
            self.assertIn("already exists", out)


# ---------------------------------------------------------------------------
# Stale locks
# ---------------------------------------------------------------------------


class StaleLockTest(unittest.TestCase):
    def test_a_lock_left_by_a_dead_process_is_broken(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            # A pid that is not running, recorded as ours so the host matches.
            dead = json.dumps({"pid": 999999, "host": socket.gethostname(),
                               "at": "2020-01-01T00:00:00Z"})
            Path(path + ".lock").write_text(dead, encoding="utf-8")
            code, out = run("start", path, "1.0.1")
            self.assertEqual(code, 0)
            self.assertIn("stale lock", out)
            self.assertFalse(Path(path + ".lock").exists())

    def test_a_live_holder_is_respected(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            mine = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                               "at": "2020-01-01T00:00:00Z"})
            Path(path + ".lock").write_text(mine, encoding="utf-8")
            lock = jsonio.FileLock(path, timeout=0.1)
            with self.assertRaises(jsonio.LockTimeout):
                lock.__enter__()

    def test_an_unreachable_holder_ages_out(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            # No host match, so the pid says nothing: only age can decide.
            other = json.dumps({"pid": os.getpid(), "host": "some-other-machine",
                                "at": "2020-01-01T00:00:00Z"})
            Path(path + ".lock").write_text(other, encoding="utf-8")
            with jsonio.FileLock(path, timeout=0.1, stale_after=0.0) as lock:
                self.assertIsNotNone(lock.broke_stale_lock)

    def test_a_pre_lease_bare_pid_lock_is_still_understood(self):
        with TempTodo(todo(phase("1.0", task("1.0.1")))) as path:
            Path(path + ".lock").write_text("999999", encoding="utf-8")
            self.assertEqual(jsonio.FileLock(path).holder(), {"pid": 999999})

    def test_pid_probing_never_signals_the_process(self):
        # os.kill(pid, 0) terminates the process on Windows; this must not use it.
        with mock.patch("taskerkeeper.jsonio.os.kill") as killed:
            jsonio._pid_alive(os.getpid())
        if os.name == "nt":
            killed.assert_not_called()


class TestSessions(unittest.TestCase):
    """Heartbeat store behind POST /api/<slug>/heartbeat (1.1.3)."""

    def test_upsert_then_list_shows_agent_not_stale(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sessions.db")
            row = sessions.upsert_heartbeat(db, "worker-1", "demo", "1.0.1",
                                            repo="r", branch="main",
                                            host="h", model="m")
            self.assertEqual(row["agent_id"], "worker-1")
            self.assertEqual(row["task_id"], "1.0.1")
            self.assertFalse(row["stale"])
            listed = sessions.list_sessions(db, "demo")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["agent_id"], "worker-1")
            self.assertFalse(listed[0]["stale"])

    def test_aged_row_shows_stale(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sessions.db")
            sessions.upsert_heartbeat(db, "worker-1", "demo", "1.0.1")
            import sqlite3

            conn = sqlite3.connect(db)
            try:
                conn.execute("UPDATE sessions SET last_seen = ? WHERE agent_id = ?",
                             ("2020-01-01T00:00:00Z", "worker-1"))
                conn.commit()
            finally:
                conn.close()
            listed = sessions.list_sessions(db, "demo")
            self.assertTrue(listed[0]["stale"])

    def test_clear_task_keeps_row_drops_task(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sessions.db")
            sessions.upsert_heartbeat(db, "worker-1", "demo", "1.0.1")
            cleared = sessions.clear_task(db, "worker-1")
            assert cleared is not None
            self.assertIsNone(cleared["task_id"])
            self.assertFalse(cleared["stale"])
            self.assertIsNone(sessions.clear_task(db, "unknown-agent"))

    def test_upsert_requires_agent_id(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                sessions.upsert_heartbeat(str(Path(tmp) / "s.db"), "  ")

    def test_list_scopes_by_slug(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sessions.db")
            sessions.upsert_heartbeat(db, "a", "one")
            sessions.upsert_heartbeat(db, "b", "two")
            self.assertEqual([r["agent_id"] for r in sessions.list_sessions(db, "one")],
                             ["a"])
            self.assertEqual(len(sessions.list_sessions(db)), 2)
            self.assertEqual(sessions.list_sessions(db, "missing"), [])


class TestServeHeartbeat(unittest.TestCase):
    """Serve wiring: heartbeat upserts, start heartbeats, done/reset clears."""

    def _registry(self, tmp: str) -> tuple[str, str]:
        todo_path = str(Path(tmp) / "todo.json")
        Path(todo_path).write_text(
            json.dumps(todo(phase("1.0", task("1.0.1")))), encoding="utf-8")
        registry = str(Path(tmp) / "registry.json")
        Path(registry).write_text(json.dumps({"projects": [
            {"slug": "demo", "name": "Demo", "repo_url": "",
             "todo_path": todo_path, "default_branch": "main"},
        ]}), encoding="utf-8")
        return registry, str(Path(tmp) / "sessions.db")

    def test_heartbeat_post_upserts_session(self):
        with TemporaryDirectory() as tmp:
            registry, db = self._registry(tmp)
            status, payload = serve.handle_post(
                "/api/{slug}/heartbeat", "demo",
                {"agent_id": "worker-1", "task_id": "1.0.1", "branch": "main"},
                registry, db)
            self.assertEqual(status, 200)
            self.assertEqual(payload["session"]["agent_id"], "worker-1")
            self.assertEqual(payload["session"]["task_id"], "1.0.1")
            listed = sessions.list_sessions(db, "demo")
            self.assertEqual(len(listed), 1)

    def test_start_auto_heartbeats_done_clears(self):
        with TemporaryDirectory() as tmp:
            registry, db = self._registry(tmp)
            status, _ = serve.handle_post(
                "/api/{slug}/start", "demo", {"task": "1.0.1", "owner": "worker-1"},
                registry, db)
            self.assertEqual(status, 200)
            row = sessions.get_session(db, "worker-1")
            assert row is not None
            self.assertEqual(row["task_id"], "1.0.1")
            status, _ = serve.handle_post(
                "/api/{slug}/done", "demo", {"task": "1.0.1", "owner": "worker-1"},
                registry, db)
            self.assertEqual(status, 200)
            row = sessions.get_session(db, "worker-1")
            assert row is not None
            self.assertIsNone(row["task_id"])

    def test_reset_clears_task(self):
        with TemporaryDirectory() as tmp:
            registry, db = self._registry(tmp)
            serve.handle_post("/api/{slug}/start", "demo",
                              {"task": "1.0.1", "owner": "worker-1"}, registry, db)
            status, _ = serve.handle_post(
                "/api/{slug}/reset", "demo", {"task": "1.0.1", "owner": "worker-1"},
                registry, db)
            self.assertEqual(status, 200)
            row = sessions.get_session(db, "worker-1")
            assert row is not None
            self.assertIsNone(row["task_id"])

    def test_sessions_db_defaults_beside_registry(self):
        self.assertTrue(
            serve.sessions_db_for_registry("deploy/registry.json").endswith(
                "sessions.db"))
        with mock.patch.dict(os.environ, {"TK_SESSIONS_DB": "/srv/x.db"}):
            self.assertEqual(serve.sessions_db_for_registry("deploy/registry.json"),
                             "/srv/x.db")

    def test_sessions_get_lists_slug(self):
        with TemporaryDirectory() as tmp:
            registry, db = self._registry(tmp)
            sessions.upsert_heartbeat(db, "worker-1", "demo")
            status, payload = serve.handle_get(
                "/api/{slug}/sessions", "demo", {}, registry, db)
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["sessions"]), 1)


class TestServeStream(unittest.TestCase):
    """SSE live stream (1.2.2): replay, since, Last-Event-ID, frame format."""

    def _registry(self, tmp: str) -> tuple[str, str, str]:
        todo_path = str(Path(tmp) / "todo.json")
        Path(todo_path).write_text(
            json.dumps(todo(phase("1.0", task("1.0.1")))), encoding="utf-8")
        registry = str(Path(tmp) / "registry.json")
        Path(registry).write_text(json.dumps({"projects": [
            {"slug": "demo", "name": "Demo", "repo_url": "",
             "todo_path": todo_path, "default_branch": "main"},
        ]}), encoding="utf-8")
        return registry, todo_path, str(Path(tmp) / "sessions.db")

    def test_route_table_has_stream(self):
        self.assertIn(("GET", "/api/stream"), serve.ROUTES)

    def test_format_sse_frame(self):
        frame = serve.format_sse({"event": "done", "task": "1.0.1"}, seq=7)
        self.assertTrue(frame.startswith("id: 7\n"))
        self.assertIn('data: {"event": "done", "task": "1.0.1"}', frame)
        self.assertTrue(frame.endswith("\n\n"))
        # Transport-only _seq never leaks into data.
        framed = serve.format_sse({"event": "done", "_seq": 7}, seq=7)
        self.assertNotIn("_seq", framed.split("data:", 1)[1])

    def test_replay_and_since_filter(self):
        with TemporaryDirectory() as tmp:
            registry, todo_path, db = self._registry(tmp)
            cli.record_event(todo_path, "start", task="1.0.1", owner="w")
            cli.record_event(todo_path, "done", task="1.0.1", owner="w")
            all_events = serve.collect_stream_events(["demo"], registry)
            self.assertEqual(len(all_events), 2)
            self.assertEqual([e["_seq"] for e in all_events], [1, 2])
            replay = serve.stream_events_since(["demo"], 1, registry)
            self.assertEqual(len(replay), 1)
            self.assertEqual(replay[0]["event"], "done")
            frame = serve.format_sse(replay[0], replay[0]["_seq"])
            parsed = json.loads(frame.split("data:", 1)[1].strip())
            self.assertEqual(parsed["task"], "1.0.1")

    def test_parse_params_supports_last_event_id(self):
        slugs, since = serve.parse_stream_params(
            {"slugs": "a, b"}, {"Last-Event-ID": "4"})
        self.assertEqual(slugs, ["a", "b"])
        self.assertEqual(since, 4)
        _, since_q = serve.parse_stream_params(
            {"since": "2"}, {"Last-Event-ID": "9"})
        self.assertEqual(since_q, 2)
