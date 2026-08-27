#!/usr/bin/env python3
"""
taskerkeeper.py — CLI tool for managing TaskerKeeper JSON todo files.

Usage:
    taskerkeeper.py validate <todo.json>          Validate against schema
    taskerkeeper.py next <todo.json>              Find next task
    taskerkeeper.py done <todo.json> <task_id>    Mark task done
    taskerkeeper.py list <todo.json>              List all tasks
    taskerkeeper.py parallel <todo.json>          Show parallel groups
    taskerkeeper.py add <todo.json> <phase_id>    Add a new task
    taskerkeeper.py convert <todo.md> [--to-json] Convert between formats

Requirements: pip install jsonschema
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

try:
    from jsonschema import validate, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "todo-v1.schema.json"


def load_todo(path: str) -> dict:
    """Load and parse a todo JSON file."""
    with open(path) as f:
        return json.load(f)


def save_todo(data: dict, path: str):
    """Save todo data back to file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def validate_todo(path: str) -> bool:
    """Validate a todo file against the schema."""
    if not HAS_JSONSCHEMA:
        print("Warning: jsonschema not installed. Install with: pip install jsonschema")
        print("Performing basic JSON parse check...")
        try:
            load_todo(path)
            print(f"✓ Valid JSON: {path}")
            return True
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON: {e}")
            return False

    data = load_todo(path)
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)

    try:
        validate(instance=data, schema=schema)
        print(f"✓ Valid: {path} matches the schema")
        return True
    except ValidationError as e:
        print(f"✗ Invalid: {e.message}")
        print(f"  Path: {list(e.absolute_path)}")
        return False


def find_next(data: dict) -> dict | None:
    """Find the next task with all prerequisites satisfied."""
    all_tasks = [t for p in data["phases"] for t in p["tasks"]]
    done_ids = {t["id"] for t in all_tasks if t["status"] == "done"}

    # Find pending tasks where all prereqs are done
    candidates = []
    for task in all_tasks:
        if task["status"] != "pending":
            continue
        prereqs = task.get("prerequisites", [])
        if all(p in done_ids for p in prereqs):
            candidates.append(task)

    if not candidates:
        return None

    # Sort by: number of unsatisfied prereqs (fewer = higher priority), then by ID
    candidates.sort(key=lambda t: (len(t.get("prerequisites", [])), t["id"]))
    return candidates[0]


def mark_done(data: dict, task_id: str) -> bool:
    """Mark a task as done. Returns True if task was found and updated."""
    for phase in data["phases"]:
        for task in phase["tasks"]:
            if task["id"] == task_id:
                task["status"] = "done"
                task["updated_at"] = datetime.now(timezone.utc).isoformat()
                return True
    return False


def list_tasks(data: dict):
    """List all tasks with their status."""
    print("Task Status Overview")
    print("═" * 50)

    status_icons = {
        "done": "✓",
        "in_progress": "►",
        "pending": "·",
        "cancelled": "✗",
        "moved": "→",
    }

    for phase in data["phases"]:
        print(f"\nPhase {phase['id']}: {phase['title']}")
        print("─" * 50)
        for task in phase["tasks"]:
            icon = status_icons.get(task["status"], "?")
            print(f"  [{icon}] {task['id']} — {task['title']}")

    # Summary
    all_tasks = [t for p in data["phases"] for t in p["tasks"]]
    print(f"\nSummary:")
    print(f"  Total: {len(all_tasks)}")
    for status in ["done", "in_progress", "pending", "cancelled"]:
        count = sum(1 for t in all_tasks if t["status"] == status)
        print(f"  {status.replace('_', ' ').title()}: {count}")


def show_parallel(data: dict):
    """Show parallel groups."""
    print("Parallel Groups")
    print("═" * 50)

    groups = {}
    for task in [t for p in data["phases"] for t in p["tasks"]]:
        group = task.get("parallel_group")
        if group:
            groups.setdefault(group, []).append(task)

    if not groups:
        print("No parallel groups defined.")
        return

    for group_name, tasks in groups.items():
        print(f"\nGroup: {group_name}")
        print("─" * 50)
        for task in tasks:
            print(f"  {task['id']} — {task['title']} [{task['status']}]")
        print("  → All tasks in this group can run in parallel when deps are met")


def show_deps(data: dict, task_id: str):
    """Show dependency chain for a task."""
    all_tasks = [t for p in data["phases"] for t in p["tasks"]]
    task = next((t for t in all_tasks if t["id"] == task_id), None)

    if not task:
        print(f"Error: task {task_id} not found")
        return

    print(f"Dependency chain for {task_id}")
    print("═" * 50)
    print(f"Task: {task['id']} — {task['title']}")
    print(f"Status: {task['status']}")
    print()

    prereqs = task.get("prerequisites", [])
    print("Prerequisites:")
    if not prereqs:
        print("  (none)")
    else:
        for p in prereqs:
            pt = next((t for t in all_tasks if t["id"] == p), None)
            status = pt["status"] if pt else "unknown"
            print(f"  • {p} [{status}]")

    print()
    print("Tasks this unblocks:")
    blockers = [t for t in all_tasks if task_id in t.get("prerequisites", [])]
    if not blockers:
        print("  (none)")
    else:
        for t in blockers:
            print(f"  • {t['id']} — {t['title']} [{t['status']}]")


def add_task(data: dict, phase_id: str, title: str, goal: str = "") -> dict | None:
    """Add a new task to a phase."""
    for phase in data["phases"]:
        if phase["id"] == phase_id:
            # Find highest task number in this phase
            existing_nums = [int(t["id"].split(".")[-1]) for t in phase["tasks"]]
            next_num = max(existing_nums, default=0) + 1
            task_id = f"{phase_id}.{next_num}"

            new_task = {
                "id": task_id,
                "title": title,
                "status": "pending",
                "goal": goal,
                "touches": [],
                "success": [],
                "prerequisites": [],
                "complexity": "Medium",
                "agent": "mid_dev_agent",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            phase["tasks"].append(new_task)
            return new_task
    return None


def main():
    parser = argparse.ArgumentParser(description="TaskerKeeper CLI")
    parser.add_argument("command", choices=["validate", "next", "done", "list", "parallel", "deps", "add"])
    parser.add_argument("todo_file", help="Path to todo JSON file")
    parser.add_argument("task_id", nargs="?", help="Task ID (for done/deps)")
    parser.add_argument("--title", help="Task title (for add)")
    parser.add_argument("--goal", help="Task goal (for add)")
    parser.add_argument("--phase", help="Phase ID (for add)")

    args = parser.parse_args()

    if args.command == "validate":
        success = validate_todo(args.todo_file)
        sys.exit(0 if success else 1)

    data = load_todo(args.todo_file)

    if args.command == "next":
        task = find_next(data)
        if not task:
            print("No pending tasks with satisfied prerequisites.")
            print("\nRemaining tasks:")
            all_tasks = [t for p in data["phases"] for t in p["tasks"]]
            for t in all_tasks:
                if t["status"] == "pending":
                    prereqs = t.get("prerequisites", [])
                    print(f"  {t['id']} — {t['title']} [deps: {', '.join(prereqs) or 'none'}]")
            sys.exit(1)

        print(f"Next task: {task['id']} — {task['title']}")
        print(f"\nGoal:\n{task.get('goal', 'No goal specified')}")
        print(f"\nComplexity: {task.get('complexity', 'unset')} | Agent: {task.get('agent', 'unset')}")
        if task.get("touches"):
            print(f"\nOwned paths:")
            for p in task["touches"]:
                print(f"  • {p}")
        if task.get("success"):
            print(f"\nSuccess criteria:")
            for s in task["success"]:
                print(f"  ✓ {s}")

    elif args.command == "done":
        if not args.task_id:
            print("Error: task ID required")
            sys.exit(1)
        if mark_done(data, args.task_id):
            save_todo(data, args.todo_file)
            print(f"✓ Task {args.task_id} marked as done.")
        else:
            print(f"Error: task {args.task_id} not found")
            sys.exit(1)

    elif args.command == "list":
        list_tasks(data)

    elif args.command == "parallel":
        show_parallel(data)

    elif args.command == "deps":
        if not args.task_id:
            print("Error: task ID required")
            sys.exit(1)
        show_deps(data, args.task_id)

    elif args.command == "add":
        if not args.phase or not args.title:
            print("Error: --phase and --title required for add")
            sys.exit(1)
        task = add_task(data, args.phase, args.title, args.goal or "")
        if task:
            save_todo(data, args.todo_file)
            print(f"✓ Added task {task['id']} — {task['title']}")
        else:
            print(f"Error: phase {args.phase} not found")
            sys.exit(1)


if __name__ == "__main__":
    main()
