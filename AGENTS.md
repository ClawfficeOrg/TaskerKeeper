# AGENTS.md

Rules for any agent working **on** this repository. This is the authoritative
rules file; `CLAUDE.md` only points here.

## What this project is

TaskerKeeper is a JSON todo format plus a Python CLI for driving autonomous
agents through a task DAG. A todo file describes a milestone as phases, each
phase holding tasks with stable IDs, prerequisites, and parallel groups. The
CLI answers one question well: *what can be worked on right now?*

It is a tool for agents, so the output contract matters as much as the logic.
Every read command has a `--json` mode; do not break it.

## Layout

```
taskerkeeper/cli.py                    All CLI logic. One file on purpose.
taskerkeeper/agents.py                 Tier -> provider/model resolution
taskerkeeper/jsonio.py                 Atomic writes and the file lock
taskerkeeper/__main__.py               python -m taskerkeeper
taskerkeeper/schema/todo-v1.schema.json  JSON Schema (draft 2020-12), package data
scripts/taskerkeeper.py                Backward-compatible shim, no logic
ralph/ralph-json.sh                    Thin wrapper that execs the CLI, no logic
skills/taskerkeeper/SKILL.md           Hermes skill describing the format
tests/test_cli.py                      stdlib unittest suite
examples/                              simple-project, zoidmatter-v7
docs/                                  philosophy, integration-guide, memory, notes
```

## Commands you will need

```bash
python -m unittest discover -s tests -v     # the whole suite, under a second
taskerkeeper agents show                    # resolved tier -> provider/model
taskerkeeper validate examples/simple-project.json
pip install .                               # non-editable install must keep working
```

Run both before claiming any change works. `pip install .` matters because the
schema is package data; a change that moves it out of `taskerkeeper/` bricks
every non-editable install.

## Rules

**One implementation of the DAG logic.** It lives in `taskerkeeper/cli.py`.
`ralph/ralph-json.sh` and `scripts/taskerkeeper.py` are forwarding shims and
must stay that way. A second implementation in bash/jq existed once, drifted
from the Python within a single release, and disagreed about task ordering and
prerequisite enforcement. Do not reintroduce one.

**Scheduling semantics are the contract.** A task is runnable only when it is
`pending`, every task in its `prerequisites` is `done`, and every phase in its
phase's `prerequisites` is complete. A phase is complete when all its tasks are
`done`, `cancelled`, or `moved`. Only `done` satisfies a task prerequisite.
Changing any of this changes what every downstream agent does — update
`ready_tasks`, `blockers_for`, the tests, and the README table together.

**Writes are locked and atomic.** Mutating commands go through
`with FileLock(path):`, reload inside the lock, and save via `write_json`, which
writes a temp file and renames. Both live in `taskerkeeper/jsonio.py`. Parallel
agents are the entire point; a plain `open(path, "w")` reintroduces lost updates.

**TaskerKeeper never calls a provider.** `agents.py` resolves a tier to a
provider/model string and hands it to whoever is dispatching. Do not add an SDK
dependency, an API key lookup, or a network call — the supervisor owns that.
Model IDs in `DEFAULT_TIERS` are data, not endorsements; keep them current but
do not build logic around specific ones.

**Config layering is last-wins, and the order is fixed:** built-in, user, repo,
todo file, then a task's own `provider`/`model`. Each config layer contributes
twice — the preset it names, then its own per-tier settings — so an
`agents set` beats an `agents use` in the same scope. `agents show` reports the
source of every resolved setting; keep that attribution working when you touch
`resolve_tiers` or `layers`.

**Every provider preset covers every tier.** `PROVIDER_PRESETS` is keyed by
provider, and each preset must define all four tiers in the schema's `agent`
enum, with its own name as the `provider` on each. A test enforces both. Adding
a provider is a new entry there and a row in the README table — no code.

**Validation has two layers.** JSON Schema checks shape; `semantic_errors`
checks meaning — duplicate IDs, dangling prerequisites, cycles, task IDs that
disagree with their phase, `moved` without `moved_to`. New structural
invariants belong in `semantic_errors` with a test, not in prose.

**Timestamps are `...Z`.** `utc_now()` produces them. Examples use the same
form; do not write `+00:00`.

**Keep the glyphs** (`✓ ✗ ► · → ═ ─ •`). stdout/stderr are reconfigured to
UTF-8 with `errors="replace"` at import, which is what makes them safe on
Windows cp1252 consoles. Do not strip to ASCII, and do not remove the
reconfigure block.

**Docs must match the code.** This repo previously advertised commands that did
not exist. If you add, rename, or remove a command, update `README.md`,
`skills/taskerkeeper/SKILL.md`, `docs/integration-guide.md`, and `CHANGELOG.md`
in the same change.

**Every behavior change gets a test.** `tests/test_cli.py` is stdlib
`unittest`, so it runs with no dev dependencies. Keep it that way.

## Conventions

- Python ≥ 3.10, `from __future__ import annotations`, 4-space indent, type
  hints on function signatures.
- Tests that touch agent config must set `TASKERKEEPER_CONFIG_HOME` to a temp
  directory. A test that reads the developer's real `~/.config` is a bug.
- The only runtime dependency is `jsonschema`. Do not add more without a reason
  that survives "could the stdlib do this".
- Comments explain *why*, not *what*. The existing ones flag the bug a piece of
  code prevents; match that.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Bump `version` in `pyproject.toml` and `__version__` in
  `taskerkeeper/__init__.py` together, and add a `CHANGELOG.md` entry.

## Working on a TaskerKeeper todo file

When driving a project's roadmap (this repo's own, or another's):

```bash
taskerkeeper ready docs/todo-v7.json --json   # fan these out in parallel
taskerkeeper start docs/todo-v7.json 7.0.1    # claim before working
taskerkeeper done docs/todo-v7.json 7.0.1 --changelog "Added the Go SDK"
```

Claim with `start` before doing the work, so a second agent does not pick up the
same task. If a session dies mid-task, the task stays `in_progress` — `next`
surfaces it first for resumption, and `reset` returns it to `pending`.

Never renumber task IDs. Insert new tasks with new IDs; retire old ones with
`status moved --moved-to <id>` or `status cancelled`.

## Known gaps

Tracked in `docs/memory.md` under "Known Gaps". `docs/notes.md` is an external
code review of v0.1; the issues it raises were fixed in v0.2, so read it as
history rather than a task list.
