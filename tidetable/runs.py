"""Live run state for the TKRun unattended runner.

Reads what the runner leaves on disk — `<repo>/.tkrun/` (or a legacy
`.overnight/`): the `pid`, the `STOP`/`KILL` sentinels and the append-only
`log.md`. It answers one question for a sidebar or an operator: *is a run
live, and on what?* It never writes and never schedules; the runner owns
both. The control-directory names live here so `tkrun` and `sidebar` agree
on where to look.
"""

from __future__ import annotations

import re
from pathlib import Path

from tidetable.jsonio import _pid_alive

#: The runner's control directory, and the pre-0.11 name read as a fallback.
RUN_DIR_NAME = ".tkrun"
LEGACY_RUN_DIR_NAME = ".overnight"

_LOG_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2}) run \(pid (\d+)\)")
_LOG_WORKTREE = re.compile(r"on ([\w./-]+); deadline ([\d:]+)")


def control_dir(root: str | Path) -> Path:
    """Where `stop`/`kill`/the pid file live: `.tkrun/`, else a legacy `.overnight/`.

    A new run uses `.tkrun/`; an existing `.overnight/` (a run started before the
    rename, or a checkout that has not migrated) is used when `.tkrun/` is absent.
    """
    root = Path(root)
    current = root / RUN_DIR_NAME
    legacy = root / LEGACY_RUN_DIR_NAME
    return legacy if (not current.exists() and legacy.exists()) else current


def _read_log(log_file: Path) -> dict:
    """The newest run's header, worktree line and last event from the append-only log."""
    info: dict = {}
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return info
    header_at = -1
    for i, line in enumerate(lines):
        if _LOG_HEADER.match(line):
            header_at = i
    if header_at < 0:
        return info
    m = _LOG_HEADER.match(lines[header_at])
    info["date"], info["pid"] = m.group(1), int(m.group(2))
    for line in lines[header_at:]:
        w = _LOG_WORKTREE.search(line)
        if w:
            info["branch"], info["deadline"] = w.group(1), w.group(2)
        if line.startswith("- "):
            info["last"] = line[2:].strip()
        if " END (" in line:
            info["ended"] = True
    return info


def run_state(root: str | Path) -> dict | None:
    """Describe the run recorded under `root`, or None when there is nothing.

    `status` is one of: `running` (pid alive, no END), `finished` (an END line),
    `stopped`/`killed` (a sentinel is present and nothing is alive), or
    `stale` (a pid file whose process is gone and no END). Unknown liveness
    (a platform that cannot tell) is reported as alive rather than dead, so a
    live run is never hidden.
    """
    root = Path(root)
    ctl = control_dir(root)
    if not ctl.exists():
        return None
    pid_file = ctl / "pid"
    stop = (ctl / "STOP").exists()
    kill = (ctl / "KILL").exists()
    raw = pid_file.read_text(encoding="ascii", errors="replace").strip() if pid_file.exists() else ""
    pid = int(raw) if raw.isdigit() else None
    log = _read_log(ctl / "log.md")
    if pid is None:
        pid = log.get("pid")          # the header records it even between runs
    alive = _pid_alive(pid) if pid is not None else None
    ended = bool(log.get("ended"))
    if kill:
        status = "killed"
    elif stop and not alive:
        status = "stopped"
    elif ended and not alive:
        status = "finished"
    elif alive:
        status = "running"
    elif pid is not None:
        status = "stale"
    else:
        status = "finished" if ended else "unknown"
    return {
        "status": status,
        "pid": pid,
        "alive": alive,
        "date": log.get("date"),
        "branch": log.get("branch"),
        "deadline": log.get("deadline"),
        "stop_requested": stop,
        "kill_requested": kill,
        "last": log.get("last", ""),
        "dir": ctl.name,
    }


def find_state(start: str | Path) -> dict | None:
    """Walk up from `start` looking for a `.tkrun/` or `.overnight/` control dir."""
    d = Path(start).resolve()
    for _ in range(64):
        state = run_state(d)
        if state is not None:
            return state
        if d.parent == d:
            return None
        d = d.parent
    return None
