"""`tidetable tui`: binary lookup and the command line it builds (never launches anything)."""

import tempfile
import unittest
from pathlib import Path

from tidetable import cli, tui


def parse(*argv):
    return cli.build_parser().parse_args(["tui", *argv])


class TestTuiCommand(unittest.TestCase):
    def test_tmux_side_splits_horizontally_at_40_columns(self):
        argv = tui.build_command("/bin/tt-tui", parse("--tmux", "--mode", "side"), {"TMUX": "/tmp/tmux-1/default,1,0"})
        self.assertEqual(argv, ["tmux", "split-window", "-h", "-l", "40", "/bin/tt-tui", "--mode", "side"])

    def test_tmux_bottom_splits_vertically_at_10_rows(self):
        argv = tui.build_command("/bin/tt-tui", parse("--tmux", "--mode", "bottom"), {"TMUX": "x"})
        self.assertEqual(argv[:5], ["tmux", "split-window", "-v", "-l", "10"])
        self.assertEqual(argv[5:], ["/bin/tt-tui", "--mode", "bottom"])

    def test_tmux_without_a_mode_defaults_to_a_side_pane(self):
        argv = tui.build_command("t", parse("--tmux"), {"TMUX": "x"})
        self.assertEqual(argv, ["tmux", "split-window", "-h", "-l", "40", "t", "--mode", "side"])

    def test_outside_tmux_or_without_the_flag_runs_in_place(self):
        self.assertEqual(tui.build_command("t", parse("--tmux", "--mode", "side"), {}), ["t", "--mode", "side"])
        self.assertEqual(tui.build_command("t", parse(), {"TMUX": "x"}), ["t"])
        self.assertEqual(tui.build_command("t", parse("--tmux", "--mode", "standard"), {"TMUX": "x"}), ["t", "--mode", "standard"])

    def test_extra_arguments_are_forwarded_to_the_binary(self):
        argv = tui.build_command("t", parse("--", "docs/todo.json", "--read-only"), {})
        self.assertEqual(argv, ["t", "docs/todo.json", "--read-only"])

    def test_missing_binary_names_how_to_build_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(tui.TuiError) as ctx:
                tui.find_binary({}, which=lambda _: None, root=Path(tmp))
        self.assertIn("cargo build --release --manifest-path tui/Cargo.toml", str(ctx.exception))

    def test_binary_lookup_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tui" / "target" / "release" / (tui.BIN + (".exe" if tui.sys.platform == "win32" else ""))
            exe.parent.mkdir(parents=True)
            exe.write_text("")
            self.assertEqual(tui.find_binary({}, which=lambda _: None, root=Path(tmp)), str(exe))
            self.assertEqual(tui.find_binary({}, which=lambda _: "/usr/bin/tidetable-tui", root=Path(tmp)), "/usr/bin/tidetable-tui")
            self.assertEqual(tui.find_binary({"TIDETABLE_TUI": str(exe)}, which=lambda _: "/other", root=Path(tmp)), str(exe))
            with self.assertRaises(tui.TuiError):
                tui.find_binary({"TIDETABLE_TUI": str(exe) + ".nope"}, root=Path(tmp))


if __name__ == "__main__":
    unittest.main()
