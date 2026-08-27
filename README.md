# TaskerKeeper

**Structured task management for autonomous agents.**

JSON-based alternative to markdown todo files. Stable IDs, dependency DAG, parallel execution groups, and semver version mapping. Built for [ralph](https://github.com/ClawfficeOrg/Zoid) but works with any autonomous agent.

## Why?

Markdown todo files (`docs/todo-v*.md`) are great for humans but painful for agents:

- **Inserting tasks breaks ordering** — IDs are positional, so adding `v9.0` between `v8.x` and `v10.0` requires renumbering everything below
- **No dependency expression** — prerequisites are prose, not machine-readable
- **No parallel support** — everything is linear by design
- **Token waste** — agents read the whole file even for one task

TaskerKeeper solves all of these with structured JSON.

## Quick Start

```bash
# Install (standalone)
git clone https://github.com/ClawfficeOrg/TaskerKeeper.git
cd TaskerKeeper
pip install -e .

# Or use directly
python scripts/taskerkeeper.py validate my-todo.json
python scripts/taskerkeeper.py next my-todo.json
python scripts/taskerkeeper.py done my-todo.json 7.0.1
```

## Features

| Feature | Markdown | TaskerKeeper JSON |
|---------|----------|-------------------|
| Task ordering | Positional (line number) | ID-based (stable) |
| Inserting a task | Resequence all below | Add anywhere, no renumber |
| Dependencies | Prose in goal text | `prerequisites: [...]` array |
| Parallel work | Impossible | `parallel_group` field |
| Version mapping | Manual/disconnected | `release.version` on phase |
| Machine parsing | Regex/line-based | Native JSON |

## Schema

The JSON Schema is at `schema/todo-v1.schema.json`. Validate any todo file:

```bash
python scripts/taskerkeeper.py validate docs/todo-v7.json
```

## For Ralph

TaskerKeeper ships with a JSON-aware ralph variant at `ralph/ralph-json.sh`. It reads the JSON todo file, finds the next task with all prerequisites satisfied, and outputs it in ralph's expected format.

```bash
# Find next task
./ralph/ralph-json.sh next docs/todo-v7.json

# Mark task done
./ralph/ralph-json.sh done docs/todo-v7.json 7.0.1

# Show parallel groups
./ralph/ralph-json.sh parallel docs/todo-v7.json
```

## For Hermes

TaskerKeeper includes a Hermes skill at `skills/taskerkeeper/SKILL.md`. Install it to teach your agent how to read, write, and manage TaskerKeeper files:

```bash
# Copy to your Hermes skills directory
cp -r skills/taskerkeeper ~/.hermes/skills/
```

## Version Mapping

Task IDs are planning references, not release versions. The release version lives on the final phase of a milestone:

```
Planning milestone (roadmap)     →    Release version (semver)
─────────────────────────────         ──────────────────────────
todo-v7.md (internal roadmap)    →    v0.7.0 (git tag)
todo-v8.md                       →    v0.8.0
...                               →    ...
todo-v1.0.md                     →    v1.0.0 (first stable)
```

## Project Structure

```
TaskerKeeper/
├── schema/
│   └── todo-v1.schema.json    # JSON Schema definition
├── ralph/
│   └── ralph-json.sh          # JSON-aware ralph variant
├── scripts/
│   └── taskerkeeper.py         # CLI tool (validate, next, done, convert)
├── skills/
│   └── taskerkeeper/
│       └── SKILL.md            # Hermes skill for AI models
├── docs/
│   ├── philosophy.md           # Why this exists
│   └── integration-guide.md    # ZoidMatter + standalone setup
├── examples/
│   ├── simple-project.json     # Minimal example
│   └── zoidmatter-v7-example.json  # Real-world example
└── README.md
```

## Integration with ZoidMatter

TaskerKeeper is designed to work as a built-in for ZoidMatter projects. See [docs/integration-guide.md](docs/integration-guide.md) for setup instructions.

## License

MIT
