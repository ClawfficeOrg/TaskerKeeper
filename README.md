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
git clone https://github.com/ClawfficeOrg/TaskerKeeper.git
cd TaskerKeeper
pip install .          # or: pip install -e .  for development

taskerkeeper validate my-todo.json
taskerkeeper ready my-todo.json          # everything runnable right now
taskerkeeper start my-todo.json 7.0.1    # claim it
taskerkeeper done my-todo.json 7.0.1     # finish it
```

Without installing, `python -m taskerkeeper <command>` and `python scripts/taskerkeeper.py <command>` both work from a checkout.

## Commands

| Command | What it does |
|---------|--------------|
| `validate <file>` | JSON Schema check **plus** semantic checks: duplicate IDs, dangling prerequisites, cycles, task/phase ID mismatches |
| `ready <file>` | Every task that can start right now — the list to fan out across parallel agents |
| `next <file>` | The single task to pick up. Resumes an `in_progress` task if one exists, else the lowest-numbered ready task |
| `start <file> <id>` | Claim a task (`pending` → `in_progress`). Refuses if prerequisites are unmet |
| `done <file> <id>` | Finish a task, report what it unblocked, file its `--changelog` line under the phase release |
| `reset <file> <id>` | Return a task to `pending` — how you recover a task orphaned by a crashed session |
| `status <file> <id> <status>` | Set any status, including `cancelled` and `moved --moved-to <id>` |
| `list <file>` | Status overview per phase, with a summary |
| `parallel <file>` | Parallel groups, marking which members are runnable now |
| `deps <file> <id>` | What a task waits on and what it unblocks |
| `add <file> --phase <id> --title <t>` | Append a task. Accepts `--goal`, `--prereq` (repeatable), `--complexity`, `--agent`, `--parallel-group`, `--touches`, `--success` |
| `convert <file>` | Render the JSON as markdown for human review (one-way) |

Every read command takes `--json`, so agents parse structured output instead of scraping box-drawing characters:

```bash
taskerkeeper ready docs/todo-v7.json --json
```

## Scheduling Rules

A task is runnable when **all** of these hold:

1. its `status` is `pending`,
2. every ID in its `prerequisites` belongs to a task with `status: "done"`, and
3. every phase in its phase's `prerequisites` is complete.

A phase is complete when none of its tasks will be worked on again — every task is `done`, `cancelled`, or `moved`. Only `done` satisfies a *task* prerequisite; `validate` warns when a prerequisite is `cancelled` or `moved`, because dependents would block forever.

Writes take a `<file>.lock` and land atomically, so parallel agents cannot silently overwrite each other.

## Features

| Feature | Markdown | TaskerKeeper JSON |
|---------|----------|-------------------|
| Task ordering | Positional (line number) | ID-based (stable) |
| Inserting a task | Resequence all below | Add anywhere, no renumber |
| Dependencies | Prose in goal text | `prerequisites: [...]` array |
| Parallel work | Impossible | `parallel_group` field + `ready` |
| Version mapping | Manual/disconnected | `release.version` on phase |
| Machine parsing | Regex/line-based | Native JSON, `--json` output |

## Schema

The JSON Schema ships inside the package at `taskerkeeper/schema/todo-v1.schema.json`, so it resolves from any install. Validate any todo file:

```bash
taskerkeeper validate docs/todo-v7.json
```

## For Ralph

`ralph/ralph-json.sh` is a thin wrapper around the CLI — same commands, same semantics, no `jq` required. (It used to be a second implementation of the DAG logic; the two drifted, so there is now one.)

```bash
./ralph/ralph-json.sh ready docs/todo-v7.json --json
./ralph/ralph-json.sh start docs/todo-v7.json 7.0.1
./ralph/ralph-json.sh done docs/todo-v7.json 7.0.1
```

## For Hermes

TaskerKeeper includes a Hermes skill at `skills/taskerkeeper/SKILL.md`:

```bash
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
├── taskerkeeper/
│   ├── cli.py                  # All CLI logic
│   ├── __main__.py             # python -m taskerkeeper
│   └── schema/
│       └── todo-v1.schema.json # JSON Schema, shipped as package data
├── ralph/
│   └── ralph-json.sh           # Thin wrapper around the CLI
├── scripts/
│   └── taskerkeeper.py         # Backward-compatible shim
├── skills/
│   └── taskerkeeper/
│       └── SKILL.md            # Hermes skill for AI models
├── tests/
│   └── test_cli.py             # stdlib unittest suite
├── docs/
│   ├── philosophy.md           # Why this exists
│   ├── integration-guide.md    # ZoidMatter + standalone setup
│   └── memory.md               # Project state for agents
├── examples/
│   ├── simple-project.json     # Minimal example
│   └── zoidmatter-v7-example.json  # Real-world example
├── AGENTS.md                   # Rules for agents working on this repo
└── pyproject.toml
```

## Tests

```bash
python -m unittest discover -s tests -v
```

No dev dependencies — stdlib `unittest`. `pytest` runs the same suite if you prefer it.

## Integration with ZoidMatter

See [docs/integration-guide.md](docs/integration-guide.md).

## License

MIT
