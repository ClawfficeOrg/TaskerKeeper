# TaskerKeeper Integration Guide

## Standalone Usage

TaskerKeeper works as a standalone tool. No frameworks or platforms required.

### Quick Start

```bash
git clone https://github.com/ClawfficeOrg/TaskerKeeper.git
cd TaskerKeeper
pip install .

# Validate a todo file (schema + semantic checks)
taskerkeeper validate my-todo.json

# Everything that can start right now
taskerkeeper ready my-todo.json

# The one task to pick up (resumes your own in_progress task if there is one)
taskerkeeper next my-todo.json

# Claim it, then finish it
taskerkeeper start my-todo.json 7.0.1
taskerkeeper done my-todo.json 7.0.1 --changelog "Added the Go SDK"

# Show all tasks / one dependency chain
taskerkeeper list my-todo.json
taskerkeeper deps my-todo.json 7.0.1
```

From a checkout without installing, `python -m taskerkeeper ...` and
`python scripts/taskerkeeper.py ...` behave identically.

### Using ralph-json.sh

`ralph/ralph-json.sh` forwards every argument to the CLI — same commands, same
semantics, no `jq` needed. It exists so ralph configs that call a shell script
keep working.

```bash
./ralph/ralph-json.sh ready my-todo.json --json
./ralph/ralph-json.sh start my-todo.json 7.0.1
./ralph/ralph-json.sh done my-todo.json 7.0.1
./ralph/ralph-json.sh list my-todo.json
./ralph/ralph-json.sh deps my-todo.json 7.0.1
```

## Integration with ZoidMatter

TaskerKeeper is designed to work as a built-in component for ZoidMatter projects.
When a project uses ZoidMatter, TaskerKeeper provides the structured task format
that ralph consumes.

### Setup

1. **Install the CLI** (the schema travels with it — you do not copy it around):
   ```bash
   pip install taskerkeeper
   ```

2. **Create your first todo file**:
   ```bash
   cp examples/simple-project.json your-project/docs/todo-v1.json
   ```

3. **Install the Hermes skill** (optional, for AI-assisted task management):
   ```bash
   cp -r skills/taskerkeeper ~/.hermes/skills/
   ```

4. **Point ralph at the JSON variant** in your `AGENTS.md` or ralph config:
   use `ralph-json.sh` (or `taskerkeeper` directly) instead of `ralph.sh` for
   JSON todo files.

### Migration from Markdown

Automatic markdown → JSON conversion is not implemented, by decision: the hard
part of a migration is deciding what prose prerequisites actually meant, and a
parser would guess. Model the roadmap as JSON using the schema (see
`examples/simple-project.json`), then:

```bash
taskerkeeper validate docs/todo-v7.json
```

The reverse direction is automated — render JSON as markdown for human review:

```bash
taskerkeeper convert docs/todo-v7.json -o docs/todo-v7.md
```

### File Layout

A typical ZoidMatter project with TaskerKeeper:

```
your-project/
├── docs/
│   ├── todo-v7.json              # Current roadmap (JSON)
│   ├── todo-v7.md                # Rendered for humans (optional)
│   ├── plan.md                   # Architecture overview
│   ├── memory.md                 # Session log
│   └── learnings.md              # Technical discoveries
├── AGENTS.md                     # Agent instructions
└── skills/
    └── taskerkeeper/
        └── SKILL.md              # Hermes skill
```

## Choosing Providers and Models

Each task names an agent tier; the tier resolves to a provider and model:

```bash
taskerkeeper agents providers                               # presets on offer
taskerkeeper agents use opencode-go                         # switch every tier at once
taskerkeeper agents show                                    # current mapping + sources
taskerkeeper agents set mid_dev_agent --model claude-sonnet-5
taskerkeeper agents set pro_dev_agent --model claude-opus-5 --scope repo
taskerkeeper agents unset mid_dev_agent --key model
taskerkeeper agents path --scope repo                       # which file that writes
```

A preset is a whole tier table for one provider:

| Tier | `anthropic` | `opencode-go` |
|------|-------------|---------------|
| `basic_dev_agent` | `claude-haiku-4-5` | `glm-5.3-flash` |
| `mid_dev_agent` | `claude-sonnet-5` | `glm-5.3-flash` |
| `pro_dev_agent` | `claude-opus-5` | `deepseek-v4-pro` |
| `flagship` | `claude-fable-5-1` | `qwen3.8-max` |

Layers, last wins:

1. the built-in `anthropic` preset
2. `~/.config/taskerkeeper/agents.json` (`--scope user`, the default)
3. `<repo>/.taskerkeeper/agents.json` (`--scope repo`)
4. the todo file's `agent_config` (`--scope todo --todo <file>`)
5. a task's own `provider` / `model`

Within a scope, the preset it names applies first and its own `tiers` entries
override it — so `agents use opencode-go` followed by
`agents set mid_dev_agent --model glm-5.3-pro` leaves the other three tiers on
the preset.

Model choice is normally a property of the machine running the agents, not of
the roadmap, so the user scope is the right home for it. Use the repo or todo
scope when a project or a milestone genuinely needs to pin something; commit
`.taskerkeeper/agents.json` when you want everyone on the project to inherit it.

Settings TaskerKeeper does not know about pass through untouched:

```bash
taskerkeeper agents set pro_dev_agent --option effort=xhigh --option speed=fast
```

TaskerKeeper never calls a provider — it resolves the strings and puts them in
`--json` output for whatever dispatches your agents.

## Parallel Agents

`ready` is the command that makes parallel execution work. It returns every task
whose task **and** phase prerequisites are satisfied:

```bash
taskerkeeper ready docs/todo-v7.json --json
```

```json
{
  "ready": [
    { "id": "7.0.1", "title": "Go client SDK", "parallel_group": "sdks",
      "agent": "mid_dev_agent", "provider": "anthropic", "model": "claude-sonnet-5" },
    { "id": "7.0.2", "title": "Ruby client SDK", "parallel_group": "sdks",
      "agent": "mid_dev_agent", "provider": "anthropic", "model": "claude-sonnet-5" }
  ],
  "in_progress": [],
  "blocked": [
    { "id": "7.1.1", "title": "Helm chart", "blocked_by": ["task 7.0.1", "phase 7.0"] }
  ]
}
```

A supervisor dispatches one worker per entry in `ready`, using the `provider`
and `model` each entry carries. Writes take a `<file>.lock` and land atomically,
so concurrent `start`/`done` calls do not lose each other's updates.

### Dispatch the disjoint set, not the ready set

`ready` says what is *runnable*. It does not say those tasks can run at the same
time — 7.0.1 and 7.0.2 may both be unblocked and both own `src/client.go`.
`--disjoint` filters to a set that is safe to dispatch together, using each
task's `touches`:

```bash
taskerkeeper ready docs/todo-v7.json --disjoint --json
```

```json
{
  "ready": [ { "id": "7.0.1", "touches": ["packages/go/"] } ],
  "disjoint": true,
  "deferred": [ { "id": "7.0.2", "conflicts_with": ["7.0.1"] } ],
  "conflicts": [ { "a": "7.0.1", "b": "7.0.2", "paths": ["packages/go/ ~ packages/go/client.go"] } ]
}
```

Tasks already `in_progress` hold their paths too, so a worker joining a running
fleet does not collide with work in flight. Without `--disjoint` the overlaps
are still reported, as a warning.

### Claims and leases

Each worker calls `start` before touching code, under its own name:

```bash
TASKERKEEPER_OWNER=worker-3 taskerkeeper start docs/todo-v7.json 7.0.1
```

That records `claimed_by`, `claimed_at`, and `lease_expires_at` on the task, and
those are enforced: a second worker's `start` on the same task fails, `next`
will not hand out a live claim, and `done` on someone else's claim needs
`--force`. Set the lease with `--lease MINUTES` or `TASKERKEEPER_LEASE_MINUTES`
(default 60) — long enough to cover your slowest task.

If a worker dies mid-task, its task stays `in_progress` until the lease lapses,
after which any worker may take it — `next` surfaces it first. `reset <id>`
returns it to `pending` immediately if you would rather not wait. A crashed
worker's `<file>.lock` is broken automatically once its process is confirmed
gone.

### Auditing a run

```bash
taskerkeeper history docs/todo-v7.json --task 7.0.1
```

Every transition appends a line to `<file>.events.jsonl` with the owner and
timestamp, so a fleet that misbehaved overnight can be reconstructed. Disable
with `TASKERKEEPER_EVENTS=0`.

## Serving the Hub

`taskerkeeper serve` exposes the same DAG logic over HTTP for remote workers
and the read-only dashboard. It is the single writer: GET routes
(`ready`, `next`, `list`, `deps`, `history`, `validate`, `parallel`) call the
CLI functions and return the `--json` shapes; POST routes (`start`, `done`,
`reset`, `status`, `add`) hold a server lock plus `FileLock`, reload inside
the lock, and enforce claim leases. `POST /api/<slug>/heartbeat` records agent
presence (repo, branch, task) with a 180s staleness horizon, and
`GET /api/stream` replays the event log as SSE. Auth is a shared
`TK_API_TOKEN` bearer today, per-project machine tokens
(`taskerkeeper/tokens.py`) next. Postgres (`deploy/migrations/001_init.sql`)
is a read projection tailed by `taskerkeeper/projector.py`, never a scheduler.

```bash
TK_API_TOKEN=... taskerkeeper serve --port 8471 --registry deploy/registry.json
curl -H "Authorization: Bearer $TK_API_TOKEN" localhost:8471/api/taskerkeeper/ready
```

## Harness Sidebars

`taskerkeeper sidebar <file>` emits one budgeted payload every harness panel
renders: **current** (in-progress with owner, claim age, goal, touches),
**concurrent** (the disjoint set, safe to fan out), **upcoming** (deferred
plus blocked with reasons), then the **current-phase tree** and the
**overall** phase tree. `--width` truncates lines, `--height` caps rows;
sections that do not fit report `more` counts. Touches are basenamed so no
absolute path leaks into a shared sidebar. Thin adapters under
`integrations/` shell out to it and never reimplement scheduling:

| Harness | Path | Mechanism |
|---------|------|-----------|
| OpenCode | `integrations/opencode/` | TUI plugin, `sidebar_content` slot |
| Pi | `integrations/pi/` | Extension panel + `/tk-sidebar`; also loads under oh-my-pi `omp` |
| OpenChamber | `integrations/openchamber/` | Rail panel over the serve API, task attach |
| Paseo | `integrations/paseo/` | Sidebar item + workspace panel via server RPC |

```bash
taskerkeeper sidebar docs/todo-v7.json --width 80 --height 40
TASKERKEEPER_TODO=docs/todo-v7.json taskerkeeper sidebar --json
```

## CI/CD Integration

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Validate any changed todo JSON files
for file in $(git diff --cached --name-only | grep '\.json$'); do
    if grep -q '"schema_version"' "$file" 2>/dev/null; then
        taskerkeeper validate "$file" || exit 1
    fi
done
```

### GitHub Actions

```yaml
# .github/workflows/validate-todos.yml
name: Validate Todo Files
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install taskerkeeper
      - name: Validate todo JSON files
        run: |
          for f in docs/todo-*.json; do
            [ -f "$f" ] && taskerkeeper validate "$f"
          done
```

`validate` exits non-zero on dangling prerequisites, cycles, duplicate IDs, and
task IDs that disagree with their phase — the failures that make a roadmap
silently unrunnable.

## Advanced Usage

### Dependency Graph Analysis

```bash
# What a task waits on and what it unblocks
taskerkeeper deps my-todo.json 7.0.1 --json

# Which parallel-group members are runnable right now
taskerkeeper parallel my-todo.json
```

### Adding Tasks Programmatically

```bash
taskerkeeper add my-todo.json --phase 7.1 \
  --title "Add logging" \
  --goal "Emit structured logs with level filtering." \
  --prereq 7.0.1 --prereq 7.0.2 \
  --complexity High --agent pro_dev_agent \
  --parallel-group observability \
  --touches src/log.rs \
  --success "Logs include level and timestamp"
```

The new ID is the phase's highest sequence number plus one. Unknown
prerequisites are rejected rather than written.

### Batch Status Updates

```bash
for id in 7.0.1 7.0.2 7.0.3; do
  taskerkeeper done my-todo.json "$id"
done
```
