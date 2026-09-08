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

# The one task to pick up (resumes an in_progress task if there is one)
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
taskerkeeper agents show                                    # current mapping + sources
taskerkeeper agents set mid_dev_agent --model claude-sonnet-5
taskerkeeper agents set pro_dev_agent --model claude-opus-5 --scope repo
taskerkeeper agents unset mid_dev_agent --key model
taskerkeeper agents path --scope repo                       # which file that writes
```

Layers, last wins:

1. built-in defaults
2. `~/.config/taskerkeeper/agents.json` (`--scope user`, the default)
3. `<repo>/.taskerkeeper/agents.json` (`--scope repo`)
4. the todo file's `agent_config.tiers` (`--scope todo --todo <file>`)
5. a task's own `provider` / `model`

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
and `model` each entry carries. Each worker calls
`start` before touching code, so no two workers claim the same task. Writes take
a `<file>.lock` and land atomically, so concurrent `start`/`done` calls do not
lose each other's updates.

If a worker dies mid-task, its task stays `in_progress`. `next` surfaces such a
task first so the work resumes; `reset <id>` returns it to `pending` if it should
be handed to someone else.

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
