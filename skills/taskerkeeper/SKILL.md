---
name: taskerkeeper
description: "Use when creating, reading, or managing TaskerKeeper JSON todo files. Structured task management with dependency DAG, parallel groups, and semver mapping."
version: 2.0.0
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
- Dispatching several agents at once across a runnable task set
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

Prefer the CLI over reimplementing this: `taskerkeeper ready <file> --json` for
the whole runnable set, `taskerkeeper next <file> --json` for a single task.

The rule the CLI applies, if you must reason about it directly:

1. Load the JSON file
2. Collect all task IDs with `status: "done"`, and all phases whose tasks are
   all `done` / `cancelled` / `moved`
3. A task is runnable when `status: "pending"`, every entry in its
   `prerequisites` is in the done set, AND every entry in its phase's
   `prerequisites` is a complete phase
4. An `in_progress` task means a session claimed it and may have crashed —
   resume it before starting anything new
5. If nothing is runnable, report which prerequisite each pending task waits on

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

### Claiming and Marking Tasks Done

1. `start` the task first (`pending` → `in_progress`) so no other agent claims it
2. On completion set `status: "done"` and update `updated_at` (`...Z` format)
3. Check if this unblocks any other tasks (their prereqs are now all met)
4. Report unblocked tasks

Use `taskerkeeper start` / `taskerkeeper done` rather than editing the file
directly: they take a lock, write atomically, refuse unmet prerequisites without
`--force`, and file the task's `--changelog` line under the phase release.

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
- **Phase prerequisites gate too** — a task with no prereqs of its own is still blocked if its phase requires an incomplete phase
- **Only `done` satisfies a prerequisite** — a `cancelled` or `moved` prerequisite blocks its dependents forever; `validate` warns about it
- **Don't hand-edit while agents are running** — the CLI locks the file; a raw write loses updates

## CLI Usage

```bash
# Validate: JSON Schema plus semantic checks (dangling prereqs, cycles, dupes)
taskerkeeper validate docs/todo-v7.json

# Everything runnable right now — fan these out across parallel agents
taskerkeeper ready docs/todo-v7.json --json

# One task to pick up (resumes an in_progress task if there is one)
taskerkeeper next docs/todo-v7.json --json

# Claim, finish, or recover a task
taskerkeeper start docs/todo-v7.json 7.0.1
taskerkeeper done docs/todo-v7.json 7.0.1 --changelog "Added the Go SDK"
taskerkeeper reset docs/todo-v7.json 7.0.1

# Retire a task without deleting it
taskerkeeper status docs/todo-v7.json 7.0.5 cancelled
taskerkeeper status docs/todo-v7.json 7.0.6 moved --moved-to 8.0.1

# List all / parallel groups / one dependency chain
taskerkeeper list docs/todo-v7.json
taskerkeeper parallel docs/todo-v7.json
taskerkeeper deps docs/todo-v7.json 7.0.1

# Add a task (new ID is the phase's max sequence + 1)
taskerkeeper add docs/todo-v7.json --phase 7.2 --title "New feature"   --goal "..." --prereq 7.0.1 --complexity High --agent pro_dev_agent

# Render for human review (one-way)
taskerkeeper convert docs/todo-v7.json -o docs/todo-v7.md
```

Every read command accepts `--json`. Parse that, not the box-drawing output.

## Ralph Integration

`ralph/ralph-json.sh` forwards every argument to the CLI, so ralph configs that
call a shell script keep working. No `jq` required.

```bash
./ralph/ralph-json.sh ready docs/todo-v7.json --json
./ralph/ralph-json.sh start docs/todo-v7.json 7.0.1
./ralph/ralph-json.sh done docs/todo-v7.json 7.0.1
```

## See Also

- `ralph-todo-format` skill — markdown todo format (legacy)
- `development-workflows` skill — broader dev workflow context
- Schema: `taskerkeeper/schema/todo-v1.schema.json` (ships with the package)
