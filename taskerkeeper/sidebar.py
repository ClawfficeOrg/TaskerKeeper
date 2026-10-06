"""Budgeted sidebar payload for harness integrations.

Every harness sidebar (OpenCode, Pi/oh-my-pi, OpenChamber, Paseo) shows the
same five sections in the same order: current work, concurrent work,
upcoming work, the current phase as a tree, and all phases as a tree.
Sections that do not fit the room report a more-count instead of silently
dropping tasks, so a narrow sidebar never looks complete when it is not.

All scheduling answers come from taskerkeeper.cli — this module formats,
never decides. Touches are basenamed (privacy: no absolute paths leak into
a shared sidebar).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from taskerkeeper import cli

#: Fixed caps for the three task sections; trees split what is left.
CURRENT_CAP = 6
CONCURRENT_CAP = 4
UPCOMING_CAP = 5
MIN_TREE_LINES = 3


def _truncate(text: str, width: int) -> str:
    """Fit one line into width, marking the cut."""
    text = str(text).replace("\n", " ")
    if width < 2 or len(text) <= width:
        return text
    return text[:max(0, width - 1)] + "…"


def _basename(path: str) -> str:
    """Last path component, forward slashes tolerated on any OS."""
    return str(path).replace("\\", "/").rstrip("/").split("/")[-1]


def claim_age(task: dict, now: datetime | None = None) -> str:
    """How long the current claim has held, e.g. 12m, 3h, 2d. Empty when unclaimed."""
    claimed_at = task.get("claimed_at")
    if not claimed_at:
        return ""
    try:
        then = datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    mins = max(0, int(((now or datetime.now(timezone.utc)) - then).total_seconds() // 60))
    if mins < 60:
        return f"{mins}m"
    if mins < 60 * 24:
        return f"{mins // 60}h"
    return f"{mins // (60 * 24)}d"


def entry(task: dict, width: int, blocked_by: list[str] | None = None) -> dict:
    """One sidebar row's data. Goal is first-line only; room decides the rest."""
    goal = str(task.get("goal", "")).split("\n")[0].strip()
    row: dict = {
        "id": task.get("id", "?"),
        "title": _truncate(task.get("title", ""), width),
        "icon": cli.STATUS_ICONS.get(task.get("status"), "?"),
        "status": task.get("status", ""),
    }
    if task.get("claimed_by"):
        row["owner"] = task["claimed_by"]
        age = claim_age(task)
        if age:
            row["age"] = age
    if goal:
        row["goal"] = _truncate(goal, width)
    touches = [_basename(p) for p in task.get("touches", [])]
    if touches:
        row["touches"] = touches
    if blocked_by:
        row["blocked_by"] = blocked_by
    return row


def _cap(items: list, limit: int) -> tuple[list, int]:
    """Cut a list to limit, reporting how many were hidden."""
    if limit < 0:
        limit = 0
    return items[:limit], max(0, len(items) - limit)


def _tree_lines(phase: dict, width: int, limit: int) -> tuple[list[str], int]:
    lines = [
        f"[{cli.STATUS_ICONS.get(t.get('status'), '?')}] "
        f"{_truncate(t.get('id', '?') + ' — ' + t.get('title', ''), width - 4)}"
        for t in phase.get("tasks", [])
    ]
    return _cap(lines, limit)


def sidebar_payload(data: dict, width: int = 80, height: int = 40) -> dict:
    """Five budgeted sections. Width truncates lines, height caps row counts."""
    width = max(20, width)
    height = max(10, height)
    running = cli.in_progress_tasks(data)
    ready = cli.ready_tasks(data)
    concurrent, deferred = cli.disjoint_tasks(ready, running)
    blocked = cli.blocked_report(data)

    current, current_more = _cap([entry(t, width) for t in running], CURRENT_CAP)
    conc, conc_more = _cap([entry(t, width) for t in concurrent], CONCURRENT_CAP)
    upcoming_rows = [entry(t, width) for t in ready if t not in concurrent]
    upcoming_rows += [
        entry({"id": b["id"], "title": b["title"], "status": "pending"},
              width, b["blocked_by"])
        for b in blocked
    ]
    upcoming, upcoming_more = _cap(upcoming_rows, UPCOMING_CAP)

    # Current phase: whatever the first current or upcoming task lives in,
    # else the first incomplete phase, else the last phase.
    anchor = (running + ready + [b for b in blocked])
    anchor_ids = {t.get("id") if isinstance(t, dict) else t.get("id") for t in anchor}
    phases = data.get("phases", [])
    current_phase = None
    for phase in phases:
        if any(t.get("id") in anchor_ids for t in phase.get("tasks", [])):
            current_phase = phase
            break
    if current_phase is None:
        incomplete = [p for p in phases if not cli.phase_is_complete(p)]
        current_phase = (incomplete or phases or [{}])[0]

    tree_budget = max(MIN_TREE_LINES, height - 22)
    overall_budget = max(MIN_TREE_LINES, height - 22 - len(phases))
    phase_tree, phase_more = _tree_lines(current_phase, width, tree_budget)
    overall = [
        f"Phase {p.get('id', '?')}: "
        f"{sum(1 for t in p.get('tasks', []) if t.get('status') == 'done')}"
        f"/{len(p.get('tasks', []))} done"
        f"{' (complete)' if cli.phase_is_complete(p) else ''}"
        for p in phases
    ]
    overall, overall_more = _cap(overall, overall_budget)

    return {
        "current": {"tasks": current, "more": current_more},
        "concurrent": {"tasks": conc, "more": conc_more,
                       "deferred": len(deferred)},
        "upcoming": {"tasks": upcoming, "more": upcoming_more},
        "phase": {"id": current_phase.get("id", "?"),
                  "title": current_phase.get("title", ""),
                  "tree": phase_tree, "more": phase_more},
        "overall": {"tree": overall, "more": overall_more},
    }


def _section(lines: list[str], title: str, rows: list[str], more: int, width: int) -> None:
    lines.append(title)
    lines.append("─" * min(width, 50))
    if not rows:
        lines.append("  (none)")
    lines.extend(f"  {r}" for r in rows)
    if more:
        lines.append(f"  …{more} more")


def render_sidebar(payload: dict, width: int = 80) -> str:
    """Plain-text render of a payload. Glyphs match list_tasks."""
    width = max(20, width)
    lines = ["TaskerKeeper", "═" * min(width, 50)]

    def fmt(t: dict) -> str:
        head = f"{t['icon']} {t['id']} — {t['title']}"
        extra = ""
        if t.get("owner"):
            extra = f" [{t['owner']}" + (f" {t['age']}" if t.get("age") else "") + "]"
        if t.get("blocked_by"):
            extra += f" [waits: {', '.join(t['blocked_by'])}]"
        first = _truncate(head + extra, width - 2)
        out = [first]
        if t.get("goal"):
            out.append(_truncate(f"  {t['goal']}", width - 2))
        if t.get("touches"):
            out.append(_truncate(f"  touches: {', '.join(t['touches'])}", width - 2))
        return "\n".join(out)

    cur = payload["current"]
    _section(lines, "Current", [fmt(t) for t in cur["tasks"]], cur["more"], width)
    con = payload["concurrent"]
    _section(lines, "Concurrent (safe to fan out)",
             [fmt(t) for t in con["tasks"]], con["more"], width)
    up = payload["upcoming"]
    _section(lines, "Upcoming", [fmt(t) for t in up["tasks"]], up["more"], width)
    ph = payload["phase"]
    _section(lines, f"Phase {ph['id']}: {ph['title']}", ph["tree"], ph["more"], width)
    ov = payload["overall"]
    _section(lines, "Overall", ov["tree"], ov["more"], width)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict:
    """Build a payload from a todo file path. Adapters call this via the CLI."""
    import argparse
    parser = argparse.ArgumentParser(prog="taskerkeeper sidebar")
    parser.add_argument("todo_file")
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=40)
    args = parser.parse_args(argv)
    data = cli.load_todo(args.todo_file)
    return sidebar_payload(data, args.width, args.height)


if __name__ == "__main__":  # pragma: no cover - CLI entry below owns this
    import json
    print(json.dumps(main(), indent=2))
