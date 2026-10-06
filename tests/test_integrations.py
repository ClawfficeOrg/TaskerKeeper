"""Tests for `taskerkeeper integrations`: installs touch only temp dirs."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from taskerkeeper import cli, integrations


def run(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class IntegrationsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "integrations"
        for n in integrations.NAMES:
            (self.src / n).mkdir(parents=True)
        (self.src / "opencode" / "tk-sidebar.ts").write_text("//")
        (self.src / "paseo" / "server.js").write_text("//")
        self.cfg = self.tmp / "cfg" / "tui.json"
        self.base = ["--source", str(self.src), "--opencode-config", str(self.cfg)]

    def test_source_missing_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            integrations.find_source(str(self.tmp / "nope"))

    def test_opencode_install_preserves_other_plugins_and_is_idempotent(self):
        self.cfg.parent.mkdir()
        self.cfg.write_text(json.dumps({"theme": "x", "plugin": ["other-plugin"]}))
        for _ in range(2):
            code, out = run("integrations", "install", "opencode", "--todo", "docs/todo.json", *self.base)
            self.assertEqual(code, 0, out)
        data = json.loads(self.cfg.read_text())
        self.assertEqual(data["theme"], "x")
        self.assertEqual(data["plugin"][0], "other-plugin")
        mine = [e for e in data["plugin"] if integrations._entry_matches(e)]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0][1], {"todoFile": str(Path("docs/todo.json").resolve())})   # absolute: opencode's cwd is not yours
        self.assertTrue(self.cfg.with_name("tui.json.bak").exists())

    def test_opencode_uninstall_removes_only_its_entry(self):
        run("integrations", "install", "opencode", *self.base)
        data = json.loads(self.cfg.read_text())
        data["plugin"].append("other-plugin")
        self.cfg.write_text(json.dumps(data))
        code, _ = run("integrations", "uninstall", "opencode", *self.base)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.cfg.read_text())["plugin"], ["other-plugin"])

    def test_opencode_refuses_to_rewrite_non_json(self):
        self.cfg.parent.mkdir()
        self.cfg.write_text('{ // comment\n "plugin": [] }')
        before = self.cfg.read_text()
        code, out = run("integrations", "install", "opencode", *self.base)
        self.assertEqual(code, 1)
        self.assertIn("not plain JSON", out)
        self.assertEqual(self.cfg.read_text(), before)

    def test_dry_run_changes_nothing(self):
        code, out = run("integrations", "install", "opencode", "--dry-run", *self.base)
        self.assertEqual(code, 0)
        self.assertIn("would write", out)
        self.assertFalse(self.cfg.exists())

    def test_paseo_needs_dir_then_copies_and_uninstall_refuses_a_copy(self):
        code, out = run("integrations", "install", "paseo", *self.base)
        self.assertEqual(code, 1)
        self.assertIn("--paseo-dir", out)
        plugins = self.tmp / "plugins"
        code, out = run("integrations", "install", "paseo", "--copy", "--paseo-dir", str(plugins), *self.base)
        self.assertEqual(code, 0, out)
        self.assertTrue((plugins / "paseo-taskerkeeper" / "server.js").exists())
        code, out = run("integrations", "uninstall", "paseo", "--paseo-dir", str(plugins), *self.base)
        self.assertEqual(code, 1)           # a copy is never deleted for you
        self.assertTrue((plugins / "paseo-taskerkeeper").exists())

    def test_paseo_symlink_roundtrip(self):
        plugins = self.tmp / "plugins"
        code, out = run("integrations", "install", "paseo", "--paseo-dir", str(plugins), *self.base)
        self.assertEqual(code, 0, out)
        dst = plugins / "paseo-taskerkeeper"
        if not dst.is_symlink():
            self.skipTest("symlinks not permitted here; fell back to a copy")
        code, out = run("integrations", "list", "--paseo-dir", str(plugins), *self.base)
        self.assertIn("linked", out)
        code, _ = run("integrations", "uninstall", "paseo", "--paseo-dir", str(plugins), *self.base)
        self.assertEqual(code, 0)
        self.assertFalse(dst.exists() or dst.is_symlink())
        self.assertTrue((self.src / "paseo" / "server.js").exists())   # source untouched

    def test_list_json_and_openchamber_is_manual(self):
        code, out = run("integrations", "list", "--json", *self.base)
        self.assertEqual(code, 0)
        rows = {r["name"]: r for r in json.loads(out)["integrations"]}
        self.assertEqual(set(rows), set(integrations.NAMES))
        self.assertEqual(rows["openchamber"]["state"], "manual")

    def test_unknown_name(self):
        code, out = run("integrations", "install", "nope", *self.base)
        self.assertEqual(code, 1)
        self.assertIn("unknown integration", out)


if __name__ == "__main__":
    unittest.main()
