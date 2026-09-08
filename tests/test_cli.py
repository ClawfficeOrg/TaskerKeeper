"""Tests for the TaskerKeeper CLI.

Stdlib unittest on purpose: `python -m unittest` works in a bare checkout with
no dev dependencies. pytest runs these too.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from taskerkeeper import agents, cli

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

    def test_unknown_tier_resolves_to_nothing(self):
        self.assertEqual(agents.resolve_task(task("1.0.1", agent="nope"), {}), {})

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
            leftovers = [p.name for p in Path(path).parent.iterdir() if p.name != "todo.json"]
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
