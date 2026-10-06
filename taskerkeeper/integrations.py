"""
`taskerkeeper integrations list|install|uninstall` — wire the harness
sidebars in `integrations/` into the tools that load them.

Installs link to the checkout (symlink; copy only as a fallback or on request)
so `git pull` updates them. It needs that checkout: a plain `pip install .`
does not ship `integrations/`, so run it from a clone or pass `--source`.

Nothing here is scheduling logic and nothing reads or writes a todo file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

NAMES = ("opencode", "pi", "paseo", "openchamber")
OPENCODE_ENTRY = "tk-sidebar.ts"


def find_source(explicit: str | None = None) -> Path:
    """The `integrations/` directory of a checkout."""
    candidates = [Path(explicit)] if explicit else [
        Path(__file__).resolve().parent.parent / "integrations",
        Path.cwd() / "integrations",
    ]
    for c in candidates:
        if all((c / n).is_dir() for n in NAMES):
            return c.resolve()
    raise ValueError("integrations/ not found beside the package (a plain `pip install .` does not "
                     "ship it). Run from a TaskerKeeper checkout or pass --source <checkout>/integrations.")


def opencode_config_path(override: str | None = None) -> Path:
    if override:
        return Path(override)
    base = os.environ.get("OPENCODE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".config" / "opencode") / "tui.json"


def _entry_matches(entry) -> bool:
    first = entry[0] if isinstance(entry, list) and entry else entry
    return isinstance(first, str) and (first.replace("\\", "/").endswith("/" + OPENCODE_ENTRY)
                                       or first == "taskerkeeper-sidebar")


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not plain JSON ({e}); edit it by hand, I won't rewrite comments away")
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.resolve() if path.is_symlink() else path     # write through a symlinked config
    if target.exists() and not target.with_name(target.name + ".bak").exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def link_dir(src: Path, dst: Path, copy: bool = False) -> str:
    """Symlink dst -> src; copy when asked or when symlinks are not permitted. Returns 'link'|'copy'."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not copy:
        try:
            os.symlink(src, dst, target_is_directory=True)
            return "link"
        except OSError:
            pass
    shutil.copytree(src, dst)
    return "copy"


def dir_state(src: Path, dst: Path) -> str:
    if dst.is_symlink():
        return "linked" if dst.resolve() == src.resolve() else "linked elsewhere"
    if dst.exists():
        return "copied"
    return "not installed"


def status(name: str, src: Path, args) -> dict:
    """Where an integration installs and whether it already is."""
    if name == "opencode":
        cfg = opencode_config_path(args.opencode_config)
        try:
            data = _read_json_object(cfg)
            on = any(_entry_matches(e) for e in data.get("plugin", []))
            return {"name": name, "target": str(cfg), "state": "installed" if on else "not installed"}
        except ValueError as e:
            return {"name": name, "target": str(cfg), "state": f"unreadable: {e}"}
    if name == "paseo":
        if not args.paseo_dir:
            return {"name": name, "target": "(pass --paseo-dir)", "state": "unknown"}
        dst = Path(args.paseo_dir) / "paseo-taskerkeeper"
        return {"name": name, "target": str(dst), "state": dir_state(src / "paseo", dst)}
    if name == "pi":
        return {"name": name, "target": "pi install " + str(src / "pi"),
                "state": "managed by pi" if shutil.which("pi") else "pi not on PATH"}
    return {"name": name, "target": "Settings -> Extensions -> Add -> folder " + str(src / "openchamber"),
            "state": "manual"}


def install_one(name: str, src: Path, args) -> str:
    if name == "opencode":
        cfg = opencode_config_path(args.opencode_config)
        data = _read_json_object(cfg)
        entry_path = (src / "opencode" / OPENCODE_ENTRY).resolve().as_posix()
        todo = str(Path(args.todo).resolve()) if args.todo else ""   # opencode's cwd is not yours
        entry = [entry_path, {"todoFile": todo}] if todo else [entry_path]
        plugins = [e for e in data.get("plugin", []) if not _entry_matches(e)]
        data.setdefault("$schema", "https://opencode.ai/tui.json")
        data["plugin"] = plugins + [entry]
        if args.dry_run:
            return f"would write {cfg}: plugin += {json.dumps(entry)}"
        _write_json(cfg, data)
        return f"wrote {cfg} (restart opencode; a .bak of the old file is kept)"
    if name == "paseo":
        if not args.paseo_dir:
            raise ValueError("paseo needs --paseo-dir <your Paseo plugins directory>")
        dst = Path(args.paseo_dir) / "paseo-taskerkeeper"
        if dst.exists() or dst.is_symlink():
            if dir_state(src / "paseo", dst) == "linked":
                return f"already linked: {dst}"
            raise ValueError(f"{dst} exists and is not a link to this checkout; remove it first")
        if args.dry_run:
            return f"would {'copy' if args.copy else 'symlink'} {src / 'paseo'} -> {dst}"
        how = link_dir(src / "paseo", dst, args.copy)
        return (f"{how}ed {dst}; enable the paseo-taskerkeeper plugin and restart Paseo. "
                "Set its todo in the plugin settings or via TASKERKEEPER_TODO")
    if name == "pi":
        cmd = ["pi", "install", str(src / "pi")]
        if args.dry_run or not shutil.which("pi"):
            return ("would run: " if args.dry_run else "pi not on PATH; run: ") + " ".join(cmd)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise ValueError(f"`{' '.join(cmd)}` failed: {r.stdout}{r.stderr}".strip())
        return "ran " + " ".join(cmd)
    return ("manual: OpenChamber installs from its UI. Settings -> Extensions -> Add -> folder, paste "
            f"{src / 'openchamber'}, then run `taskerkeeper serve` and set the token/slug under "
            "Settings -> Integrations")


def uninstall_one(name: str, src: Path, args) -> str:
    if name == "opencode":
        cfg = opencode_config_path(args.opencode_config)
        data = _read_json_object(cfg)
        keep = [e for e in data.get("plugin", []) if not _entry_matches(e)]
        if len(keep) == len(data.get("plugin", [])):
            return "not installed"
        data["plugin"] = keep
        if args.dry_run:
            return f"would remove the tk-sidebar entry from {cfg}"
        _write_json(cfg, data)
        return f"removed the tk-sidebar entry from {cfg}"
    if name == "paseo":
        if not args.paseo_dir:
            raise ValueError("paseo needs --paseo-dir <your Paseo plugins directory>")
        dst = Path(args.paseo_dir) / "paseo-taskerkeeper"
        state = dir_state(src / "paseo", dst)
        if state == "not installed":
            return "not installed"
        if state != "linked":
            raise ValueError(f"{dst} is {state}, not a link to this checkout; remove it by hand")
        if not args.dry_run:
            dst.unlink() if os.name != "nt" else os.rmdir(dst)
        return ("would remove " if args.dry_run else "removed ") + str(dst)
    if name == "pi":
        return "remove it with pi's own package manager (pi list / pi remove)"
    return "manual: remove the extension under Settings -> Extensions"


def _names(arg: str) -> list[str]:
    if arg == "all":
        return list(NAMES)
    if arg not in NAMES:
        raise ValueError(f"unknown integration '{arg}' (one of: {', '.join(NAMES)}, all)")
    return [arg]


def cmd_integrations(args) -> int:
    src = find_source(args.source)
    if args.integrations_command == "list":
        rows = [status(n, src, args) for n in NAMES]
        if args.json:
            print(json.dumps({"source": str(src), "integrations": rows}, indent=2))
        else:
            print(f"source: {src}")
            for r in rows:
                print(f"  {r['name']:<12} {r['state']:<22} {r['target']}")
        return 0
    failed = 0
    for n in _names(args.name):
        try:
            fn = install_one if args.integrations_command == "install" else uninstall_one
            print(f"{n}: {fn(n, src, args)}")
        except ValueError as e:
            failed += 1
            print(f"{n}: Error: {e}")
    return 1 if failed else 0


def add_parser(sub) -> None:
    p = sub.add_parser("integrations", help="Install the harness sidebar integrations (list|install|uninstall)")
    inner = p.add_subparsers(dest="integrations_command", required=True)

    def common(c):
        c.add_argument("--source", help="Path to a checkout's integrations/ directory")
        c.add_argument("--opencode-config", metavar="FILE",
                       help="opencode tui.json (default: $OPENCODE_CONFIG_DIR or ~/.config/opencode/tui.json)")
        c.add_argument("--paseo-dir", metavar="DIR", help="Your Paseo plugins directory")
        c.set_defaults(func=cmd_integrations)

    ls = inner.add_parser("list", help="Where each integration installs and whether it is")
    ls.add_argument("--json", action="store_true", help="Machine-readable output")
    common(ls)
    for verb, text in (("install", "Install one integration, or `all`"),
                       ("uninstall", "Remove one integration, or `all`")):
        c = inner.add_parser(verb, help=text)
        c.add_argument("name", help=f"{' | '.join(NAMES)} | all")
        c.add_argument("--dry-run", action="store_true", help="Show what would change; change nothing")
        if verb == "install":
            c.add_argument("--todo", metavar="FILE", help="Todo file the opencode sidebar reads")
            c.add_argument("--copy", action="store_true", help="Copy instead of symlink (paseo)")
        else:
            c.set_defaults(todo=None, copy=False)
        common(c)
