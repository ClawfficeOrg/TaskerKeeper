---
name: taskerkeeper
description: "Use when creating, reading, or managing TaskerKeeper JSON todo files. Structured task management with dependency DAG, parallel groups, and semver mapping."
version: 1.0.0
author: KITT (ClawfficeOrg)
license: MIT
metadata:
  hermes:
    tags: [task-management, todo, json, ralph, autonomous, project-planning]
    related_skills: [ralph-todo-format, development-workflows]
---

# TaskerKeeper Skill

Task management for autonomous agents using structured JSON instead of markdown. Stable IDs, dependency DAG, parallel execution groups, and semver version mapping.

## When to Use

- Creating todo files for projects that use ralph or other autonomous agents
- Managing tasks with dependencies that need parallel execution
- Converting existing markdown todos to structured JSON
- Reading/managing task state during development sessions

## File Format

TaskerKeeper uses JSON files (typically `docs/todo-v{N}.json`) with this structure:

```json
{
  "schema_version": "1.0.0",
  "project": {
    "name": "MyProject",
    "version": {
      "milestone": "v7",
      "release_version": "0.7.0"
    },
    "description": "Project description"
  },
  "phases": [
    {
      "id": "7.0",
      "title": "Phase Title",
      "goal": "What this phase delivers",
      "prerequisites": [],
      "tasks": [...]
    }
  ]
}
```

## Task Structure

Each task has:

```json
{
  "id": "7.0.1",
  "title": "Task title",
  "status": "pending",
  "goal": "Detailed description",
  "touches": ["file/path.rs"],
  "success": ["Criteria 1", "Criteria 2"],
  "prerequisites": ["6.0.3"],
  "parallel_group": "sdks",
  "complexity": "Medium",
  "agent": "mid_dev_agent"
}
```

### Key Fields

- **`id`**: Stable identifier. Format: `PHASE.SEQUENCE`. Never changes.
- **`status`**: `pending`, `in_progress`, `done`, `cancelled`, `moved`
- **`prerequisites`**: Task IDs that must be `done` before this can start. THIS IS THE KEY FEATURE.
- **`parallel_group`**: Tasks in the same group CAN run concurrently when all deps are met.
- **`agent`**: Which agent tier handles this: `basic_dev_agent`, `mid_dev_agent`, `pro_dev_agent`, `flagship`

## Reading Tasks

### Find Next Task

To find the next task to work on:
1. Load the JSON file
2. Collect all task IDs with `status: "done"`
3. Find tasks where `status: "pending"` AND all `prerequisites` are in the done set
4. If multiple candidates, prefer tasks in parallel groups (they can run concurrently)
5. If no candidates, check for blocked tasks and report what's holding them up

### Check Dependencies

For a task like `7.1.1` with `prerequisites: ["7.0.1", "7.0.2", "7.0.3", "7.0.4"]`:
- All four prerequisite tasks must have `status: "done"`
- Only then can `7.1.1` start

### Parallel Execution

Tasks with the same `parallel_group` value can run simultaneously:
- Group "sdks" contains tasks 7.0.1, 7.0.2, 7.0.3, 7.0.4
- All have empty prerequisites
- A parallel-aware agent can dispatch 4 workers for these

## Writing Tasks

### Adding a New Task

1. Find the phase it belongs to
2. Determine the next sequence number (max existing + 1)
3. Set the `id` as `PHASE.SEQUENCE`
4. Set `status: "pending"`
5. List any `prerequisites` (other task IDs that must complete first)
6. Assign to a `parallel_group` if it can run concurrently with others
7. Set `complexity` and `agent` tier

### Marking Tasks Done

1. Set `status: "done"`
2. Update `updated_at` timestamp
3. Check if this unblocks any other tasks (their prereqs are now all met)
4. Report unblocked tasks

### Moving Tasks

If a task needs to move to a different phase or version:
1. Set `status: "moved"` on the original task
2. Set `moved_to: "new.task.id"` pointing to the new location
3. Create the new task in the target location
4. Preserve history — the old ID still exists

## Version Mapping

Task IDs are planning references, not release versions:

```
Planning milestone (roadmap)     →    Release version (semver)
─────────────────────────────         ──────────────────────────
todo-v7.md (internal roadmap)    →    v0.7.0 (git tag)
todo-v8.md                       →    v0.8.0
```

The `release` object on the final phase of a milestone drives auto-tagging:

```json
{
  "id": "7.1",
  "release": {
    "version": "0.7.0",
    "tag_on_complete": true
  }
}
```

## Pitfalls

- **Task IDs are stable** — never renumber. Insert new tasks with new IDs.
- **Prerequisites are IDs, not phases** — `prerequisites: ["6.0.3"]` not `prerequisites: ["6.0"]`
- **Circular dependencies are invalid** — validate before committing
- **Parallel groups don't imply ordering** — tasks in different groups can still have dep relationships
- **`status: "moved"` preserves history** — don't delete tasks, move them

## CLI Usage

```bash
# Validate
python scripts/taskerkeeper.py validate docs/todo-v7.json

# Find next task
python scripts/taskerkeeper.py next docs/todo-v7.json

# Mark done
python scripts/taskerkeeper.py done docs/todo-v7.json 7.0.1

# List all
python scripts/taskerkeeper.py list docs/todo-v7.json

# Show parallel groups
python scripts/taskerkeeper.py parallel docs/todo-v7.json

# Show a task's dependency chain
python scripts/taskerkeeper.py deps docs/todo-v7.json 7.0.1

# Add task (phase via --phase; new ID is the phase's max sequence + 1)
python scripts/taskerkeeper.py add docs/todo-v7.json --phase 7.2 --title "New feature"
```

## Ralph Integration

TaskerKeeper ships with `ralph/ralph-json.sh` — a bash script that wraps the JSON reading logic for ralph:

```bash
# Find next task
./ralph/ralph-json.sh next docs/todo-v7.json

# Mark done
./ralph/ralph-json.sh done docs/todo-v7.json 7.0.1

# Show parallel groups
./ralph/ralph-json.sh parallel docs/todo-v7.json
```

## See Also

- `ralph-todo-format` skill — markdown todo format (legacy)
- `development-workflows` skill — broader dev workflow context
- Schema: `schema/todo-v1.schema.json`
