---
name: taskerkeeper
description: "Use when creating, reading, or managing TaskerKeeper JSON todo files. Structured task management with dependency DAG, parallel groups, and semver mapping."
version: 2.3.0
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
- **`touches`**: File paths this task owns. `ready --disjoint` uses them to emit a set that is safe to dispatch together — two tasks whose paths overlap are never handed out at once.
- **`parallel_group`**: A label for related work. Purely descriptive: it does not schedule, gate, or order anything. Use `prerequisites` for real ordering.
- **`complexity`**: `Low` / `Medium` / `High` / `Very High`. With no `agent`, this picks the tier (Low→basic, Medium→mid, High→pro, Very High→flagship, unless a tier's `complexity_range` says otherwise).
- **`agent`**: Which agent tier handles this: `basic_dev_agent`, `mid_dev_agent`, `pro_dev_agent`, `flagship`. Optional — derived from `complexity` when absent. The tier resolves to a concrete provider and model — run `taskerkeeper agents show` to see the mapping, and read `provider`/`model` off `ready --json` to dispatch.
- **`claimed_by`** / **`claimed_at`** / **`lease_expires_at`**: Written by `start`, cleared on any non-`in_progress` status. Do not hand-edit; use `start` and `reset`.
- **`provider`** / **`model`**: Optional per-task override, for the one task that needs a specific model. Configure the tier instead when a whole class of work should move.

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
4. An `in_progress` task means an agent claimed it. Resume it before starting
   anything new **only if the claim is yours or its `lease_expires_at` has
   passed** — otherwise another agent is working it right now
5. If nothing is runnable, report which prerequisite each pending task waits on

### Check Dependencies

For a task like `7.1.1` with `prerequisites: ["7.0.1", "7.0.2", "7.0.3", "7.0.4"]`:
- All four prerequisite tasks must have `status: "done"`
- Only then can `7.1.1` start

### Parallel Execution

Ask for the conflict-free set, do not eyeball it:

```bash
taskerkeeper ready docs/todo-v7.json --disjoint --json
```

- `ready` is everything runnable; `--disjoint` is everything runnable that does
  not collide on `touches` paths, including against tasks already in progress
- Paths collide when equal, or when one is a directory containing the other
- Skipped tasks come back under `deferred` with what they collided with
- Each worker still calls `start --owner <name>` before touching code
- `parallel_group` is a reading aid, not an input to any of this

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

`start` also records a claim — `claimed_by`, `claimed_at`, `lease_expires_at` —
and that claim is enforced:

```bash
TASKERKEEPER_OWNER=worker-3 taskerkeeper start docs/todo-v7.json 7.0.1
```

- Another agent's `start` on the same task fails while the lease holds
- `next` skips tasks another agent holds, and resumes only yours or a lapsed one
- `done` on someone else's live claim needs `--force`
- A crashed agent's task frees itself when the lease expires; `reset` frees it
  immediately

Every transition is appended to `<file>.events.jsonl`; read it with
`taskerkeeper history`.

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

The `release` object on the final phase of a milestone describes the tag:

```json
{
  "id": "7.1",
  "release": {
    "version": "0.7.0",
    "tag_on_complete": true
  }
}
```

Nothing tags automatically. Finishing the phase's last task makes `done --json`
report `"release_ready": true`; creating the tag is an explicit step:

```bash
taskerkeeper release docs/todo-v7.json 7.1        # what would be tagged
taskerkeeper release docs/todo-v7.json 7.1 --tag  # create it
```

## Pitfalls

- **Task IDs are stable** — never renumber. Insert new tasks with new IDs.
- **Prerequisites are IDs, not phases** — `prerequisites: ["6.0.3"]` not `prerequisites: ["6.0"]`
- **Circular dependencies are invalid** — validate before committing
- **Parallel groups mean nothing to the scheduler** — they neither order work nor make it safe to run together. `prerequisites` orders; `touches` + `ready --disjoint` decides what runs together
- **Never resume an `in_progress` task whose lease is still live** — someone else is on it
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

# Only what is safe to dispatch together (no overlapping owned paths)
taskerkeeper ready docs/todo-v7.json --disjoint --json

# One task to pick up (resumes your own in_progress task, never someone else's)
taskerkeeper next docs/todo-v7.json --owner worker-3 --json

# Claim, finish, or recover a task
taskerkeeper start docs/todo-v7.json 7.0.1 --owner worker-3
taskerkeeper done docs/todo-v7.json 7.0.1 --changelog "Added the Go SDK"
taskerkeeper reset docs/todo-v7.json 7.0.1

# Release for a finished phase: report, then tag
taskerkeeper release docs/todo-v7.json 7.1
taskerkeeper release docs/todo-v7.json 7.1 --tag

# Everything that ever happened to a task
taskerkeeper history docs/todo-v7.json --task 7.0.1

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

# Single-writer core API (VPS hub): same DAG logic over HTTP, never a second implementation
taskerkeeper serve --port 8471 --registry deploy/registry.json
# GET /api/<slug>/ready|next|list|deps|history|validate|parallel, POST .../start|done|reset|status|add|heartbeat, GET /api/stream (SSE)

# Harness sidebars: budgeted current/concurrent/upcoming/phase-tree/overall payload
taskerkeeper sidebar docs/todo-v7.json --width 80 --height 40 --json
# Adapters under integrations/ shell out to it: opencode (TUI sidebar_content),
# pi (extension panel, also loads under oh-my-pi omp), openchamber (rail panel
# over the serve API), paseo (sidebar item + workspace panel via server RPC)

# Which provider/model runs each tier, and where each setting came from
taskerkeeper agents show --todo docs/todo-v7.json
taskerkeeper agents providers                  # presets: anthropic, opencode-go
taskerkeeper agents use opencode-go            # switch every tier at once
taskerkeeper agents set pro_dev_agent --model claude-opus-5 --scope repo
```

## Choosing the Model for a Task

A task names an agent tier; the tier resolves to a provider and model through
layered config — the built-in `anthropic` preset, then the user config
(`~/.config/taskerkeeper/agents.json`), then the repo config
(`<repo>/.taskerkeeper/agents.json`), then the todo file's `agent_config`, and
finally a task's own `provider`/`model`. Last wins.

Each of those config layers can name a provider preset (a whole tier table —
`anthropic` or `opencode-go`) as well as individual tiers. The preset applies
first, so a hand-set tier in the same scope still wins.

`ready --json` and `next --json` carry the resolved values on every task, so a
supervisor dispatches in one lookup:

```json
{ "id": "7.0.1", "agent": "mid_dev_agent",
  "provider": "anthropic", "model": "claude-sonnet-5" }
```

Prefer moving a whole tier over pinning individual tasks — a per-task `model`
is for the genuine exception. TaskerKeeper only resolves the strings; it never
calls a provider itself.

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
