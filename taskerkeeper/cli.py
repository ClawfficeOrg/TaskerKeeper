#!/usr/bin/env python3
"""
taskerkeeper — CLI for managing TaskerKeeper JSON todo files.

Usage:
    taskerkeeper validate <todo.json>            Schema + semantic validation
    taskerkeeper next <todo.json>                Next task to work on
    taskerkeeper ready <todo.json>               Every currently-runnable task
    taskerkeeper start <todo.json> <task_id>     Claim a task (-> in_progress)
    taskerkeeper done <todo.json> <task_id>      Mark a task done
    taskerkeeper reset <todo.json> <task_id>     in_progress -> pending
    taskerkeeper status <todo.json> <task_id> <status>
                                                 Set any status (cancelled/moved/...)
    taskerkeeper list <todo.json>                Status overview
    taskerkeeper parallel <todo.json>            Parallel groups
    taskerkeeper deps <todo.json> <task_id>      Dependency chain
    taskerkeeper add <todo.json> --phase <id> --title <t>
                                                 Append a task to a phase
    taskerkeeper convert <todo.json>             Render JSON as markdown

Every read command accepts --json for machine-readable output.

Requirements: pip install jsonschema
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from taskerkeeper import agents
from taskerkeeper.jsonio import FileLock, LockTimeout, read_json, write_json

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    from jsonschema import ValidationError
    from jsonschema import validate as _jsonschema_validate

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

#: Statuses that mean the task will not be worked on again.
TERMINAL_STATUSES = frozenset({"done", "cancelled", "moved"})

#: Statuses the schema allows.
VALID_STATUSES = ("pending", "in_progress", "done", "cancelled", "moved")

STATUS_ICONS = {
    "done": "✓",
    "in_progress": "►",
    "pending": "·",
    "cancelled": "✗",
    "moved": "→",
}


def schema_path() -> Path:
    """Locate the bundled JSON Schema.

    Package data first, so a non-editable ``pip install`` works; then the
    source-checkout layouts (package dir, then the pre-0.2 repo root).
    """
    try:
        from importlib.resources import files

        packaged = Path(str(files("taskerkeeper").joinpath("schema/todo-v1.schema.json")))
        if packaged.is_file():
            return packaged
    except (ImportError, TypeError, FileNotFoundError, ModuleNotFoundError):
        pass

    here = Path(__file__).resolve().parent
    for candidate in (here / "schema" / "todo-v1.schema.json",
                      here.parent / "schema" / "todo-v1.schema.json"):
        if candidate.is_file():
            return candidate
    return here / "schema" / "todo-v1.schema.json"


SCHEMA_PATH = schema_path()


def utc_now() -> str:
    """Timestamp in the same ...Z form the examples use."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# File IO — see taskerkeeper/jsonio.py for the lock and the atomic write.
# ---------------------------------------------------------------------------


def load_todo(path: str) -> dict:
    """Load and parse a todo JSON file."""
    return read_json(path)


def save_todo(data: dict, path: str) -> None:
    """Write the todo file atomically."""
    write_json(data, path)


# ---------------------------------------------------------------------------
# Traversal helpers
# ---------------------------------------------------------------------------


def iter_tasks(data: dict):
    """Yield (phase, task) for every task, in document order."""
    for phase in data.get("phases", []):
        for task in phase.get("tasks", []):
            yield phase, task


def all_tasks(data: dict) -> list[dict]:
    return [t for _, t in iter_tasks(data)]


def id_key(task_id: str):
    """Sort key that orders 1.0.10 after 1.0.9."""
    parts = str(task_id).split(".")
    try:
        return (0, tuple(int(p) for p in parts), "")
    except ValueError:
        return (1, (), str(task_id))


def done_task_ids(data: dict) -> set[str]:
    return {t["id"] for t in all_tasks(data) if t.get("status") == "done"}


def phase_is_complete(phase: dict) -> bool:
    """True when no task in the phase will be worked on again.

    cancelled and moved count as complete — that work is not coming back.
    """
    return all(t.get("status") in TERMINAL_STATUSES for t in phase.get("tasks", []))


def complete_phase_ids(data: dict) -> set[str]:
    return {p["id"] for p in data.get("phases", []) if phase_is_complete(p)}


def blockers_for(task: dict, phase: dict, done_ids: set[str], done_phases: set[str]) -> list[str]:
    """Reasons this task cannot start yet. Empty list means runnable."""
    reasons = [f"task {p}" for p in task.get("prerequisites", []) if p not in done_ids]
    reasons += [f"phase {p}" for p in phase.get("prerequisites", []) if p not in done_phases]
    return reasons


def ready_tasks(data: dict) -> list[dict]:
    """Every pending task whose task and phase prerequisites are all satisfied."""
    done_ids = done_task_ids(data)
    done_phases = complete_phase_ids(data)
    ready = [
        task
        for phase, task in iter_tasks(data)
        if task.get("status") == "pending"
        and not blockers_for(task, phase, done_ids, done_phases)
    ]
    ready.sort(key=lambda t: id_key(t["id"]))
    return ready


def in_progress_tasks(data: dict) -> list[dict]:
    tasks = [t for t in all_tasks(data) if t.get("status") == "in_progress"]
    tasks.sort(key=lambda t: id_key(t["id"]))
    return tasks


def blocked_report(data: dict) -> list[dict]:
    """Pending tasks that are not runnable, with the reason each is stuck."""
    done_ids = done_task_ids(data)
    done_phases = complete_phase_ids(data)
    out = []
    for phase, task in iter_tasks(data):
        if task.get("status") != "pending":
            continue
        reasons = blockers_for(task, phase, done_ids, done_phases)
        if reasons:
            out.append({"id": task["id"], "title": task.get("title", ""), "blocked_by": reasons})
    out.sort(key=lambda t: id_key(t["id"]))
    return out


def find_next(data: dict, resume: bool = True) -> dict | None:
    """The single task an agent should pick up.

    An in_progress task wins: it was claimed by a session that may have crashed,
    and nothing downstream unblocks until it is finished. Otherwise the
    lowest-numbered ready task, which is what ready lists first.
    """
    if resume:
        running = in_progress_tasks(data)
        if running:
            return running[0]
    ready = ready_tasks(data)
    return ready[0] if ready else None


def find_task(data: dict, task_id: str) -> tuple[dict, dict] | tuple[None, None]:
    for phase, task in iter_tasks(data):
        if task.get("id") == task_id:
            return phase, task
    return None, None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def semantic_errors(data: dict) -> tuple[list[str], list[str]]:
    """Checks the JSON Schema cannot express. Returns (errors, warnings).

    Schema validation only proves the shape is right. These catch the failures
    that make a todo file silently unusable: an ID that no task has, a cycle no
    agent can ever break, a task filed under the wrong phase.
    """
    errors: list[str] = []
    warnings: list[str] = []

    phases = data.get("phases", [])
    phase_ids: set[str] = set()
    for phase in phases:
        pid = phase.get("id")
        if pid in phase_ids:
            errors.append(f"duplicate phase id: {pid}")
        phase_ids.add(pid)

    tasks_by_id: dict[str, dict] = {}
    for phase, task in iter_tasks(data):
        tid = task.get("id")
        if tid in tasks_by_id:
            errors.append(f"duplicate task id: {tid}")
        tasks_by_id[tid] = task

        # A task id is its phase id plus one sequence number. Anything else and
        # phase-level gating silently applies the wrong phase to the task.
        prefix = str(tid).rsplit(".", 1)[0]
        if prefix != phase.get("id"):
            errors.append(f"task {tid} sits in phase {phase.get('id')} but its id implies phase {prefix}")

    for phase in phases:
        for dep in phase.get("prerequisites", []):
            if dep not in phase_ids:
                errors.append(f"phase {phase.get('id')} requires unknown phase {dep}")

    for tid, task in tasks_by_id.items():
        for dep in task.get("prerequisites", []):
            if dep not in tasks_by_id:
                errors.append(f"task {tid} requires unknown task {dep}")
            elif dep == tid:
                errors.append(f"task {tid} requires itself")
            elif tasks_by_id[dep].get("status") in ("cancelled", "moved"):
                warnings.append(
                    f"task {tid} requires {dep}, which is {tasks_by_id[dep].get('status')} "
                    f"and can never be done — {tid} is blocked forever"
                )
        if task.get("status") == "moved":
            moved_to = task.get("moved_to")
            if not moved_to:
                errors.append(f"task {tid} is moved but has no moved_to")
            elif moved_to not in tasks_by_id:
                errors.append(f"task {tid} moved_to {moved_to}, which does not exist")

    errors.extend(f"prerequisite cycle: {' -> '.join(cycle)}" for cycle in find_cycles(tasks_by_id))
    return errors, warnings


def find_cycles(tasks_by_id: dict[str, dict]) -> list[list[str]]:
    """Every prerequisite cycle, each reported once, via iterative DFS."""
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    color: dict[str, int] = {}  # 0 = unvisited, 1 = on stack, 2 = finished

    for root in sorted(tasks_by_id, key=id_key):
        if color.get(root, 0) != 0:
            continue
        stack: list[tuple[str, list[str]]] = [(root, [])]
        while stack:
            node, path = stack.pop()
            if node == "\0pop":
                color[path[-1]] = 2
                continue
            if color.get(node, 0) == 1:
                cycle = path[path.index(node):] + [node]
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle)
                continue
            if color.get(node, 0) == 2:
                continue
            color[node] = 1
            stack.append(("\0pop", path + [node]))
            for dep in tasks_by_id[node].get("prerequisites", []):
                if dep in tasks_by_id:
                    stack.append((dep, path + [node]))
    return cycles


def validate_todo(path: str, schema_only: bool = False) -> bool:
    """Validate a todo file. Prints findings; returns True when it is usable."""
    try:
        data = load_todo(path)
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON: {e}")
        return False

    ok = True
    if HAS_JSONSCHEMA:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        try:
            _jsonschema_validate(instance=data, schema=schema)
            print(f"✓ Schema: {path} matches {SCHEMA_PATH.name}")
        except ValidationError as e:
            print(f"✗ Invalid: {e.message}")
            print(f"  Path: {list(e.absolute_path)}")
            return False
    else:
        print("Warning: jsonschema not installed. Install with: pip install jsonschema")
        print(f"✓ Parsed as JSON: {path} (schema check skipped)")

    if schema_only:
        return ok

    errors, warnings = semantic_errors(data)
    for w in warnings:
        print(f"! Warning: {w}")
    if errors:
        for e in errors:
            print(f"✗ {e}")
        return False
    print("✓ Semantics: ids unique, prerequisites resolve, no cycles")
    return ok


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def unmet_prereqs(data: dict, task: dict, phase: dict) -> list[str]:
    return blockers_for(task, phase, done_task_ids(data), complete_phase_ids(data))


def set_status(data: dict, task_id: str, status: str, moved_to: str | None = None) -> dict | None:
    """Set a task status. Returns the task, or None when the ID is unknown."""
    _, task = find_task(data, task_id)
    if task is None:
        return None
    task["status"] = status
    if moved_to:
        task["moved_to"] = moved_to
    task["updated_at"] = utc_now()
    return task


def collect_changelog(data: dict, phase: dict, task: dict) -> str | None:
    """File a finished task changelog line under the release it ships in.

    The phase release if it has one, else the last phase in the file that does
    (intermediate phases are planning groups; the milestone ships at the end).
    """
    entry = task.get("changelog")
    if not entry:
        return None

    target = phase if isinstance(phase.get("release"), dict) else None
    if target is None:
        for candidate in reversed(data.get("phases", [])):
            if isinstance(candidate.get("release"), dict):
                target = candidate
                break
    if target is None:
        return None

    entries = target["release"].setdefault("changelog_entries", [])
    if entry not in entries:
        entries.append(entry)
    return target["id"]


def unblocked_by(data: dict, task_id: str) -> list[dict]:
    """Tasks that became runnable because task_id just finished."""
    done_ids = done_task_ids(data)
    done_phases = complete_phase_ids(data)
    out = []
    for phase, task in iter_tasks(data):
        if task.get("status") != "pending":
            continue
        if task_id not in task.get("prerequisites", []):
            continue
        if not blockers_for(task, phase, done_ids, done_phases):
            out.append(task)
    out.sort(key=lambda t: id_key(t["id"]))
    return out


def next_task_id(phase: dict) -> str:
    """Next free sequence number in a phase, tolerating hand-edited IDs."""
    nums = []
    for task in phase.get("tasks", []):
        tail = str(task.get("id", "")).rsplit(".", 1)[-1]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{phase['id']}.{max(nums, default=0) + 1}"


def add_task(data: dict, phase_id: str, title: str, **fields) -> dict | None:
    """Append a task to a phase. Returns the new task, or None if no such phase."""
    for phase in data.get("phases", []):
        if phase.get("id") != phase_id:
            continue
        now = utc_now()
        task = {
            "id": next_task_id(phase),
            "title": title,
            "status": "pending",
            "goal": fields.get("goal") or "",
            "touches": list(fields.get("touches") or []),
            "success": list(fields.get("success") or []),
            "prerequisites": list(fields.get("prerequisites") or []),
            "complexity": fields.get("complexity") or "Medium",
            "agent": fields.get("agent") or "mid_dev_agent",
            "created_at": now,
            "updated_at": now,
        }
        if fields.get("parallel_group"):
            task["parallel_group"] = fields["parallel_group"]
        phase.setdefault("tasks", []).append(task)
        return task
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def task_summary(task: dict, tiers: dict[str, dict] | None = None) -> dict:
    """The task fields an agent actually consumes, for --json output.

    With `tiers`, the resolved provider/model are folded in so a supervisor can
    dispatch straight from this output.
    """
    keys = ("id", "title", "status", "goal", "touches", "success", "tests",
            "prerequisites", "parallel_group", "complexity", "agent")
    summary = {k: task[k] for k in keys if k in task}
    if tiers is not None:
        summary.update(agents.resolve_task(task, tiers))
    return summary


def print_task(task: dict, header: str, tiers: dict[str, dict] | None = None) -> None:
    print(f"{header}: {task['id']} — {task.get('title', '')}")
    print(f"\nGoal:\n{task.get('goal', 'No goal specified')}")
    print(f"\nComplexity: {task.get('complexity', 'unset')} | Agent: {task.get('agent', 'unset')}")
    if tiers is not None:
        resolved = agents.resolve_task(task, tiers)
        if resolved.get("model"):
            print(f"Model: {resolved.get('provider', '?')} / {resolved['model']}")
    if task.get("parallel_group"):
        print(f"Parallel group: {task['parallel_group']}")
    if task.get("touches"):
        print("\nOwned paths:")
        for p in task["touches"]:
            print(f"  • {p}")
    if task.get("success"):
        print("\nSuccess criteria:")
        for s in task["success"]:
            print(f"  ✓ {s}")
    if task.get("tests"):
        print(f"\nTests: {task['tests']}")


def print_blocked(data: dict) -> None:
    blocked = blocked_report(data)
    if not blocked:
        return
    print("\nBlocked tasks:")
    for item in blocked:
        print(f"  {item['id']} — {item['title']} [waiting on: {', '.join(item['blocked_by'])}]")


def list_tasks(data: dict) -> None:
    print("Task Status Overview")
    print("═" * 50)
    for phase in data.get("phases", []):
        marker = " (complete)" if phase_is_complete(phase) else ""
        print(f"\nPhase {phase['id']}: {phase.get('title', '')}{marker}")
        print("─" * 50)
        for task in phase.get("tasks", []):
            icon = STATUS_ICONS.get(task.get("status"), "?")
            print(f"  [{icon}] {task['id']} — {task.get('title', '')}")

    tasks = all_tasks(data)
    print("\nSummary:")
    print(f"  Total: {len(tasks)}")
    for status in VALID_STATUSES:
        count = sum(1 for t in tasks if t.get("status") == status)
        print(f"  {status.replace('_', ' ').title()}: {count}")


def list_payload(data: dict) -> dict:
    return {
        "phases": [
            {
                "id": p["id"],
                "title": p.get("title", ""),
                "complete": phase_is_complete(p),
                "tasks": [
                    {"id": t["id"], "title": t.get("title", ""), "status": t.get("status")}
                    for t in p.get("tasks", [])
                ],
            }
            for p in data.get("phases", [])
        ],
        "summary": {
            status: sum(1 for t in all_tasks(data) if t.get("status") == status)
            for status in VALID_STATUSES
        },
    }


def parallel_groups(data: dict) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for task in all_tasks(data):
        group = task.get("parallel_group")
        if group:
            groups.setdefault(group, []).append(task)
    return groups


def show_parallel(data: dict) -> None:
    print("Parallel Groups")
    print("═" * 50)
    groups = parallel_groups(data)
    if not groups:
        print("No parallel groups defined.")
        return
    ready_ids = {t["id"] for t in ready_tasks(data)}
    for name, tasks in groups.items():
        print(f"\nGroup: {name}")
        print("─" * 50)
        for task in tasks:
            flag = " READY" if task["id"] in ready_ids else ""
            print(f"  {task['id']} — {task.get('title', '')} [{task.get('status')}]{flag}")
        print("  → Tasks marked READY can be dispatched in parallel right now")


def deps_payload(data: dict, task_id: str) -> dict | None:
    phase, task = find_task(data, task_id)
    if task is None:
        return None
    index = {t["id"]: t for t in all_tasks(data)}
    return {
        "id": task["id"],
        "title": task.get("title", ""),
        "status": task.get("status"),
        "phase": phase["id"],
        "prerequisites": [
            {"id": p, "status": index[p].get("status") if p in index else "unknown"}
            for p in task.get("prerequisites", [])
        ],
        "phase_prerequisites": [
            {"id": p, "complete": p in complete_phase_ids(data)}
            for p in phase.get("prerequisites", [])
        ],
        "unblocks": [
            {"id": t["id"], "title": t.get("title", ""), "status": t.get("status")}
            for t in all_tasks(data)
            if task_id in t.get("prerequisites", [])
        ],
        "blocked_by": unmet_prereqs(data, task, phase),
    }


def show_deps(data: dict, task_id: str) -> bool:
    payload = deps_payload(data, task_id)
    if payload is None:
        print(f"Error: task {task_id} not found")
        return False

    print(f"Dependency chain for {task_id}")
    print("═" * 50)
    print(f"Task: {payload['id']} — {payload['title']}")
    print(f"Status: {payload['status']} | Phase: {payload['phase']}")
    print("\nPrerequisites:")
    if not payload["prerequisites"]:
        print("  (none)")
    for p in payload["prerequisites"]:
        print(f"  • {p['id']} [{p['status']}]")
    if payload["phase_prerequisites"]:
        print("\nPhase prerequisites:")
        for p in payload["phase_prerequisites"]:
            print(f"  • phase {p['id']} [{'complete' if p['complete'] else 'incomplete'}]")
    print("\nTasks this unblocks:")
    if not payload["unblocks"]:
        print("  (none)")
    for t in payload["unblocks"]:
        print(f"  • {t['id']} — {t['title']} [{t['status']}]")
    return True


def to_markdown(data: dict) -> str:
    """Render the todo file as markdown for human review (one-way, JSON -> md)."""
    project = data.get("project", {})
    version = project.get("version", {})
    lines = [f"# {project.get('name', 'Project')} — {version.get('milestone', '?')} "
             f"({version.get('release_version', '?')})", ""]
    if project.get("description"):
        lines += [project["description"], ""]

    for phase in data.get("phases", []):
        lines.append(f"## Phase {phase['id']} — {phase.get('title', '')}")
        if phase.get("goal"):
            lines += ["", phase["goal"]]
        if phase.get("prerequisites"):
            lines += ["", f"Requires phases: {', '.join(phase['prerequisites'])}"]
        release = phase.get("release")
        if isinstance(release, dict) and release.get("version"):
            lines += ["", f"Ships as **{release['version']}**"]
        lines.append("")
        for task in phase.get("tasks", []):
            box = "x" if task.get("status") == "done" else " "
            lines.append(f"- [{box}] **{task['id']}** {task.get('title', '')} "
                         f"({task.get('status', 'pending')})")
            if task.get("goal"):
                lines.append(f"  - Goal: {task['goal']}")
            if task.get("prerequisites"):
                lines.append(f"  - Requires: {', '.join(task['prerequisites'])}")
            if task.get("touches"):
                lines.append(f"  - Touches: {', '.join(task['touches'])}")
            for criterion in task.get("success", []):
                lines.append(f"  - Success: {criterion}")
            if task.get("tests"):
                lines.append(f"  - Tests: {task['tests']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def resolved_tiers(data: dict, todo_path: str) -> dict[str, dict]:
    return agents.resolve_tiers(data, todo_path)[0]


def cmd_next(args) -> int:
    data = load_todo(args.todo_file)
    tiers = resolved_tiers(data, args.todo_file)
    task = find_next(data, resume=not args.no_resume)
    if task is None:
        if args.json:
            emit_json({"task": None, "blocked": blocked_report(data)})
        else:
            print("No runnable tasks.")
            print_blocked(data)
        return 1
    if args.json:
        emit_json({"task": task_summary(task, tiers),
                   "resumed": task.get("status") == "in_progress"})
    else:
        header = "Resume task" if task.get("status") == "in_progress" else "Next task"
        print_task(task, header, tiers)
    return 0


def cmd_ready(args) -> int:
    data = load_todo(args.todo_file)
    tiers = resolved_tiers(data, args.todo_file)
    ready = ready_tasks(data)
    running = in_progress_tasks(data)
    if args.json:
        emit_json({
            "ready": [task_summary(t, tiers) for t in ready],
            "in_progress": [task_summary(t, tiers) for t in running],
            "blocked": blocked_report(data),
        })
        return 0 if ready or running else 1

    print("Ready to start now")
    print("═" * 50)
    if not ready:
        print("  (none)")
    for task in ready:
        group = f" [group: {task['parallel_group']}]" if task.get("parallel_group") else ""
        model = agents.resolve_task(task, tiers).get("model", "unset")
        print(f"  {task['id']} — {task.get('title', '')} "
              f"[{task.get('agent', 'unset')} → {model}]{group}")
    if running:
        print("\nAlready claimed (in_progress):")
        for task in running:
            print(f"  {task['id']} — {task.get('title', '')}")
    print_blocked(data)
    return 0 if ready or running else 1


def cmd_start(args) -> int:
    with FileLock(args.todo_file):
        data = load_todo(args.todo_file)
        phase, task = find_task(data, args.task_id)
        if task is None:
            print(f"Error: task {args.task_id} not found")
            return 1
        if task.get("status") == "in_progress":
            print(f"Task {args.task_id} is already in progress.")
            return 0
        if task.get("status") == "done":
            print(f"Error: task {args.task_id} is already done. Use reset to reopen it.")
            return 1
        blocked = unmet_prereqs(data, task, phase)
        if blocked and not args.force:
            print(f"Error: task {args.task_id} is blocked by: {', '.join(blocked)}")
            print("Finish those first, or pass --force to override.")
            return 1
        if blocked:
            print(f"Warning: starting {args.task_id} with unmet prerequisites: {', '.join(blocked)}")
        set_status(data, args.task_id, "in_progress")
        save_todo(data, args.todo_file)
    print(f"► Task {args.task_id} started.")
    return 0


def cmd_done(args) -> int:
    with FileLock(args.todo_file):
        data = load_todo(args.todo_file)
        phase, task = find_task(data, args.task_id)
        if task is None:
            print(f"Error: task {args.task_id} not found")
            return 1
        if task.get("status") == "done":
            print(f"Task {args.task_id} is already done.")
            return 0
        blocked = unmet_prereqs(data, task, phase)
        if blocked and not args.force:
            print(f"Error: task {args.task_id} has unmet prerequisites: {', '.join(blocked)}")
            print("Pass --force to mark it done anyway.")
            return 1
        if blocked:
            print(f"Warning: unmet prerequisites: {', '.join(blocked)} (forced)")
        if args.changelog:
            task["changelog"] = args.changelog
        set_status(data, args.task_id, "done")
        filed_under = collect_changelog(data, phase, task)
        unblocked = unblocked_by(data, args.task_id)
        phase_done = phase_is_complete(phase)
        save_todo(data, args.todo_file)

    print(f"✓ Task {args.task_id} marked as done.")
    if filed_under:
        print(f"  Changelog entry filed under phase {filed_under}.")
    if unblocked:
        print("\nUnblocked tasks:")
        for task in unblocked:
            print(f"  → {task['id']} — {task.get('title', '')}")
    if phase_done:
        release = phase.get("release")
        print(f"\nPhase {phase['id']} is complete.")
        if isinstance(release, dict) and release.get("tag_on_complete"):
            print(f"  Release {release.get('version', '?')} is ready to tag.")
    return 0


def cmd_reset(args) -> int:
    with FileLock(args.todo_file):
        data = load_todo(args.todo_file)
        _, task = find_task(data, args.task_id)
        if task is None:
            print(f"Error: task {args.task_id} not found")
            return 1
        previous = task.get("status")
        set_status(data, args.task_id, "pending")
        save_todo(data, args.todo_file)
    print(f"· Task {args.task_id} reset to pending (was {previous}).")
    return 0


def cmd_status(args) -> int:
    if args.status == "moved" and not args.moved_to:
        print("Error: --moved-to is required when setting status to moved")
        return 1
    with FileLock(args.todo_file):
        data = load_todo(args.todo_file)
        _, task = find_task(data, args.task_id)
        if task is None:
            print(f"Error: task {args.task_id} not found")
            return 1
        if args.moved_to and find_task(data, args.moved_to)[1] is None:
            print(f"Error: moved_to target {args.moved_to} does not exist")
            return 1
        previous = task.get("status")
        set_status(data, args.task_id, args.status, args.moved_to)
        save_todo(data, args.todo_file)
    print(f"{STATUS_ICONS.get(args.status, '?')} Task {args.task_id}: {previous} → {args.status}")
    return 0


def cmd_list(args) -> int:
    data = load_todo(args.todo_file)
    if args.json:
        emit_json(list_payload(data))
    else:
        list_tasks(data)
    return 0


def cmd_parallel(args) -> int:
    data = load_todo(args.todo_file)
    if args.json:
        tiers = resolved_tiers(data, args.todo_file)
        ready_ids = {t["id"] for t in ready_tasks(data)}
        emit_json({
            name: [dict(task_summary(t, tiers), ready=t["id"] in ready_ids) for t in tasks]
            for name, tasks in parallel_groups(data).items()
        })
    else:
        show_parallel(data)
    return 0


def cmd_agents(args) -> int:
    """Show or edit the tier → provider/model mapping."""
    todo_path = getattr(args, "todo", None)
    todo_data = load_todo(todo_path) if todo_path else None

    if args.agents_command == "path":
        print(agents.scope_path(args.scope, todo_path))
        return 0

    if args.agents_command == "providers":
        current, _ = agents.active_provider(todo_data, todo_path)
        if args.json:
            emit_json({"active": current, "presets": agents.PROVIDER_PRESETS})
            return 0
        print("Provider presets")
        print("═" * 62)
        for name, preset in agents.PROVIDER_PRESETS.items():
            marker = "  (active)" if name == current else ""
            print(f"\n{name}{marker}")
            for tier in sorted(preset):
                print(f"  {tier:<18} {preset[tier].get('model', '-')}")
        print("\nSwitch with: taskerkeeper agents use <provider>")
        return 0

    if args.agents_command == "use":
        if not args.clear and not args.provider:
            print("Error: name a provider, or pass --clear to drop the current one")
            return 1
        if args.clear:
            path, changed = agents.clear_provider(args.scope, todo_path)
            if not changed:
                print(f"Nothing to clear: the {args.scope} scope names no provider")
                return 1
            print(f"✓ Cleared the provider preset from the {args.scope} scope")
            print(f"  {path}")
            return 0
        if args.provider not in agents.PROVIDER_PRESETS:
            known = ", ".join(agents.PROVIDER_PRESETS)
            print(f"Error: no preset named {args.provider!r}. Known presets: {known}")
            print("Set individual tiers instead: taskerkeeper agents set <tier> "
                  "--provider ... --model ...")
            return 1
        path = agents.set_provider(args.scope, args.provider, todo_path)
        preset = agents.PROVIDER_PRESETS[args.provider]
        print(f"✓ {args.scope} scope now uses the {args.provider} preset")
        for tier in sorted(preset):
            print(f"    {tier:<18} {preset[tier].get('model', '-')}")
        print(f"  {path}")
        return 0

    if args.agents_command == "show":
        tiers, sources = agents.resolve_tiers(todo_data, todo_path)
        provider, provider_source = agents.active_provider(todo_data, todo_path)
        if args.json:
            emit_json({
                "provider": provider,
                "provider_source": provider_source,
                "tiers": {t: dict(cfg, _sources=sources.get(t, {})) for t, cfg in tiers.items()},
                "overrides": [
                    {"id": task["id"], **{k: task[k] for k in ("provider", "model") if k in task}}
                    for task in (all_tasks(todo_data) if todo_data else [])
                    if task.get("provider") or task.get("model")
                ],
            })
            return 0

        print(f"Provider preset: {provider} (from {provider_source})")
        print()
        print("Agent tiers")
        print("═" * 62)
        print(f"  {'tier':<18} {'provider':<12} {'model':<22} source")
        for tier in sorted(tiers):
            config = tiers[tier]
            origin = sources.get(tier, {})
            print(f"  {tier:<18} {config.get('provider', '-'):<12} "
                  f"{config.get('model', '-'):<22} {origin.get('model', '-')}")
            extras = {k: v for k, v in config.items() if k not in ("provider", "model")}
            for key, value in sorted(extras.items()):
                print(f"      {key} = {value} ({origin.get(key, '-')})")

        if todo_data:
            overrides = [t for t in all_tasks(todo_data) if t.get("provider") or t.get("model")]
            if overrides:
                print("\nPer-task overrides:")
                for task in overrides:
                    resolved = agents.resolve_task(task, tiers)
                    print(f"  {task['id']} → {resolved.get('provider', '?')} / "
                          f"{resolved.get('model', '?')}")
        print(f"\nConfig files (last wins):")
        print(f"  user  {agents.user_config_path()}")
        print(f"  repo  {agents.repo_config_path(todo_path)}")
        if todo_path:
            print(f"  todo  {todo_path}")
        return 0

    if args.agents_command == "set":
        settings = {}
        if args.provider:
            settings["provider"] = args.provider
        if args.model:
            settings["model"] = args.model
        for pair in args.option or []:
            key, _, value = pair.partition("=")
            if not _:
                print(f"Error: --option expects key=value, got {pair!r}")
                return 1
            settings[key] = value
        if not settings:
            print("Error: nothing to set. Pass --provider, --model, or --option key=value")
            return 1
        path = agents.set_tier(args.scope, args.tier, settings, todo_path)
        pairs = ", ".join(f"{k}={v}" for k, v in settings.items())
        print(f"✓ {args.tier}: {pairs} ({args.scope} scope)")
        print(f"  {path}")
        return 0

    if args.agents_command == "unset":
        path, changed = agents.unset_tier(args.scope, args.tier, args.key, todo_path)
        if not changed:
            print(f"Nothing to remove: {args.tier} is not set in the {args.scope} scope")
            return 1
        target = f"{args.tier} {', '.join(args.key)}" if args.key else args.tier
        print(f"✓ Removed {target} from the {args.scope} scope")
        print(f"  {path}")
        return 0

    print(f"Error: unknown agents command {args.agents_command}")
    return 1


def cmd_deps(args) -> int:
    data = load_todo(args.todo_file)
    if args.json:
        payload = deps_payload(data, args.task_id)
        if payload is None:
            emit_json({"error": f"task {args.task_id} not found"})
            return 1
        emit_json(payload)
        return 0
    return 0 if show_deps(data, args.task_id) else 1


def cmd_add(args) -> int:
    with FileLock(args.todo_file):
        data = load_todo(args.todo_file)
        for dep in args.prereq or []:
            if find_task(data, dep)[1] is None:
                print(f"Error: prerequisite {dep} does not exist")
                return 1
        task = add_task(
            data,
            args.phase,
            args.title,
            goal=args.goal,
            prerequisites=args.prereq,
            complexity=args.complexity,
            agent=args.agent,
            parallel_group=args.parallel_group,
            touches=args.touches,
            success=args.success,
        )
        if task is None:
            print(f"Error: phase {args.phase} not found")
            return 1
        save_todo(data, args.todo_file)
    print(f"✓ Added task {task['id']} — {task['title']}")
    return 0


def cmd_validate(args) -> int:
    return 0 if validate_todo(args.todo_file, schema_only=args.schema_only) else 1


def cmd_convert(args) -> int:
    data = load_todo(args.todo_file)
    markdown = to_markdown(data)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"✓ Wrote {args.output}")
    else:
        print(markdown, end="")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    from taskerkeeper import __version__

    parser = argparse.ArgumentParser(prog="taskerkeeper", description="TaskerKeeper CLI")
    parser.add_argument("--version", action="version", version=f"taskerkeeper {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_command(name, help_text, *, task_id=False, json_out=False):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("todo_file", help="Path to todo JSON file")
        if task_id:
            p.add_argument("task_id", help="Task ID")
        if json_out:
            p.add_argument("--json", action="store_true", help="Machine-readable output")
        return p

    p = add_command("validate", "Schema + semantic validation")
    p.add_argument("--schema-only", action="store_true", help="Skip the semantic checks")
    p.set_defaults(func=cmd_validate)

    p = add_command("next", "Next task to work on", json_out=True)
    p.add_argument("--no-resume", action="store_true",
                   help="Ignore in_progress tasks; return the next pending one")
    p.set_defaults(func=cmd_next)

    p = add_command("ready", "Every currently-runnable task", json_out=True)
    p.set_defaults(func=cmd_ready)

    p = add_command("start", "Claim a task (-> in_progress)", task_id=True)
    p.add_argument("--force", action="store_true", help="Start despite unmet prerequisites")
    p.set_defaults(func=cmd_start)

    p = add_command("done", "Mark a task done", task_id=True)
    p.add_argument("--force", action="store_true", help="Finish despite unmet prerequisites")
    p.add_argument("--changelog", help="Changelog line for this task")
    p.set_defaults(func=cmd_done)

    p = add_command("reset", "Return a task to pending", task_id=True)
    p.set_defaults(func=cmd_reset)

    p = add_command("status", "Set an arbitrary status", task_id=True)
    p.add_argument("status", choices=VALID_STATUSES)
    p.add_argument("--moved-to", dest="moved_to", help="Target task ID (required for moved)")
    p.set_defaults(func=cmd_status)

    p = add_command("list", "Status overview", json_out=True)
    p.set_defaults(func=cmd_list)

    p = add_command("parallel", "Parallel groups", json_out=True)
    p.set_defaults(func=cmd_parallel)

    p = add_command("deps", "Dependency chain for a task", task_id=True, json_out=True)
    p.set_defaults(func=cmd_deps)

    p = add_command("add", "Append a task to a phase")
    p.add_argument("--phase", required=True, help="Phase ID to add to")
    p.add_argument("--title", required=True, help="Task title")
    p.add_argument("--goal", help="Task goal")
    p.add_argument("--prereq", action="append", metavar="TASK_ID",
                   help="Prerequisite task ID (repeatable)")
    p.add_argument("--complexity", choices=["Low", "Medium", "High", "Very High"])
    p.add_argument("--agent", choices=["basic_dev_agent", "mid_dev_agent", "pro_dev_agent", "flagship"])
    p.add_argument("--parallel-group", dest="parallel_group", help="Parallel group name")
    p.add_argument("--touches", action="append", metavar="PATH", help="Owned path (repeatable)")
    p.add_argument("--success", action="append", metavar="CRITERION",
                   help="Success criterion (repeatable)")
    p.set_defaults(func=cmd_add)

    p = add_command("convert", "Render the todo file as markdown")
    p.add_argument("-o", "--output", help="Write to this file instead of stdout")
    p.set_defaults(func=cmd_convert)

    build_agents_parser(sub)
    return parser


def build_agents_parser(sub) -> None:
    """`taskerkeeper agents ...` — the provider/model mapping per agent tier."""
    p = sub.add_parser("agents", help="Configure which provider/model runs each tier")
    p.set_defaults(func=cmd_agents)
    inner = p.add_subparsers(dest="agents_command", required=True)

    def add_todo(cmd, help_text):
        cmd.add_argument("--todo", metavar="FILE", help=help_text)

    show = inner.add_parser("show", help="Resolved tiers and where each setting came from")
    show.add_argument("--json", action="store_true", help="Machine-readable output")
    add_todo(show, "Include this todo file as the highest-priority layer")

    providers = inner.add_parser("providers", help="List the built-in provider presets")
    providers.add_argument("--json", action="store_true", help="Machine-readable output")
    add_todo(providers, "Todo file, to report which preset is active for it")

    use = inner.add_parser("use", help="Point a scope at a provider preset")
    use.add_argument("provider", nargs="?", help=f"One of: {', '.join(agents.PROVIDER_PRESETS)}")
    use.add_argument("--clear", action="store_true", help="Drop this scope's preset instead")
    use.add_argument("--scope", choices=agents.SCOPES, default=agents.LAYER_USER,
                     help="Which config layer to edit (default: user)")
    add_todo(use, "Todo file to edit, required for --scope todo")

    for name, help_text in (("set", "Set provider/model for a tier"),
                            ("unset", "Remove a tier, or keys from it")):
        cmd = inner.add_parser(name, help=help_text)
        cmd.add_argument("tier", help="Agent tier, e.g. mid_dev_agent")
        cmd.add_argument("--scope", choices=agents.SCOPES, default=agents.LAYER_USER,
                         help="Which config layer to edit (default: user)")
        add_todo(cmd, "Todo file to edit, required for --scope todo")
        if name == "set":
            cmd.add_argument("--provider", help="Provider name, e.g. anthropic")
            cmd.add_argument("--model", help="Model ID, e.g. claude-opus-5")
            cmd.add_argument("--option", action="append", metavar="KEY=VALUE",
                             help="Any other setting to pass through (repeatable)")
        else:
            cmd.add_argument("--key", action="append",
                             help="Remove only this key (repeatable). Omit to remove the tier")

    path = inner.add_parser("path", help="Print the config file for a scope")
    path.add_argument("--scope", choices=agents.SCOPES, default=agents.LAYER_USER)
    add_todo(path, "Todo file, required for --scope todo")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
    except FileNotFoundError as e:
        print(f"Error: {e.filename} not found")
        return 1
    except json.JSONDecodeError as e:
        target = getattr(args, "todo_file", None) or getattr(args, "todo", None) or "input"
        print(f"✗ Invalid JSON in {target}: {e}")
        return 1
    except (LockTimeout, ValueError) as e:
        print(f"Error: {e}")
        return 1
    if argv is None:
        sys.exit(code)
    return code


if __name__ == "__main__":
    main()
