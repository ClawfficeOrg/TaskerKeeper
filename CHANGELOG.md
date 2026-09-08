# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-09-08

### Added

- `agents` command group — configure which provider and model handles each
  agent tier, so `task.agent` resolves to something a supervisor can dispatch.
  - `agents show` prints the resolved mapping and the layer each setting came
    from; `--todo <file>` includes the todo file's layer and lists per-task
    overrides. `--json` for machine-readable output.
  - `agents set <tier>` writes `--provider` / `--model`, plus any other setting
    via `--option key=value`, into a `--scope` of `user` (default), `repo`, or
    `todo`.
  - `agents unset <tier>` removes a tier, or `--key` one setting, from a scope.
  - `agents path --scope <scope>` prints the file that scope writes to.
- Layered configuration, last wins: built-in defaults →
  `~/.config/taskerkeeper/agents.json` (under `%APPDATA%` on Windows) →
  `<repo>/.taskerkeeper/agents.json` → the todo file's `agent_config.tiers` →
  a task's own `provider` / `model`. `TASKERKEEPER_CONFIG_HOME` relocates the
  user config.
- `next`, `ready`, and `parallel` now carry the resolved `provider` and `model`
  on every task in `--json`, and `next` prints the model in its human output —
  dispatch needs one call, not two.
- Per-task `provider` / `model` in the schema, for the task that genuinely needs
  a specific model. Configure the tier when a whole class of work should move.
- `taskerkeeper/agents.py` and `taskerkeeper/jsonio.py`; the lock and the atomic
  write moved into the latter so config writes get the same guarantees as todo
  writes. `cli.FileLock`, `cli.load_todo`, and `cli.save_todo` still work.

### Changed

- Schema: `agent_config.tiers` entries accept `provider`, `model`, and any
  additional keys; `agent_config` documents where it sits in the layering.
  `agent_config.tiers` is no longer schema-only — the CLI reads it.

## [0.2.0] — 2026-09-08

Closes the findings of the v0.1 code review in `docs/notes.md`.

### Added

- `ready` — lists every currently-runnable task, with `--json`. This is what
  makes parallel dispatch possible; `parallel_group` was display-only before.
- `start` — claims a task (`pending` → `in_progress`) so two agents cannot pick
  up the same work. `in_progress` was a dead state that nothing could set.
- `reset` — returns a task to `pending`, recovering one orphaned by a crashed
  session.
- `status` — sets any status, including `cancelled` and
  `moved --moved-to <id>`. Both were in the schema with no CLI path.
- `convert` — renders a todo file as markdown for human review (one-way).
- `--json` on every read command (`next`, `ready`, `list`, `parallel`, `deps`),
  so agents stop scraping box-drawing characters.
- Semantic validation on top of the JSON Schema: duplicate task/phase IDs,
  dangling task/phase prerequisites, prerequisite cycles, task IDs that
  disagree with their phase, `moved` without a valid `moved_to`, and a warning
  when a prerequisite is `cancelled` or `moved` (blocked forever).
- Flags on `add`: `--prereq` (repeatable, validated), `--complexity`,
  `--agent`, `--parallel-group`, `--touches`, `--success`.
- `--force` on `start` and `done`; `--changelog` on `done`.
- `tests/test_cli.py` — 37 stdlib `unittest` tests, no dev dependencies.
- `.github/workflows/ci.yml` — tests and example validation on Linux and
  Windows, Python 3.10 and 3.13, against a non-editable install.
- `python -m taskerkeeper` entry point.
- `AGENTS.md` (rules for agents working on this repo) and `CLAUDE.md`
  (a pointer to it).

### Fixed

- **`pip install .` was broken.** `SCHEMA_PATH` resolved to
  `<package parent>/schema/`, which does not exist in `site-packages`, so only
  an editable install could validate anything. The schema moved to
  `taskerkeeper/schema/todo-v1.schema.json` and ships as package data, resolved
  via `importlib.resources`.
- **Phase-level prerequisites were ignored.** `find_next` only checked task
  prerequisites, so a task in phase 1.1 with no prerequisites of its own was
  handed out while phase 1.0 was half-done.
- **`in_progress` blocked work forever.** A crashed session left a task claimed;
  nothing resumed it and nothing downstream unblocked. `next` now returns an
  `in_progress` task first, and `reset` clears one.
- **Two implementations of the DAG logic had drifted.** `ralph/ralph-json.sh`
  is now a thin wrapper that execs the CLI. It had a tautological `select`
  (line 57), a `join(", ") // "none"` that never produced `none` (line 70), and
  ordering and `done`-enforcement rules that disagreed with the Python. `jq` is
  no longer a dependency.
- **`done` did not check prerequisites at all** (the jq path warned and wrote
  anyway). It now refuses without `--force`.
- **Concurrent writes lost updates.** Mutating commands take a `<file>.lock`,
  reload inside it, and write atomically via temp file + rename.
- **`add` crashed on hand-edited IDs** — `int(t["id"].split(".")[-1])` raised
  `ValueError` on any non-numeric sequence. Non-numeric IDs are now skipped when
  computing the next number.
- **`release.changelog_entries` claimed to be auto-collected and was not.**
  `done` now files a task's `changelog` line under its phase release, or under
  the last phase that has one.
- Timestamps are written as `...Z`, matching the examples, instead of `+00:00`.
- Misleading comment in `find_next`: it sorted by total prerequisite count, not
  unsatisfied ones (every candidate has zero unsatisfied). Ordering is now
  numeric by ID, which also matches what `ready` lists first.

### Changed

- `next` returns an `in_progress` task before a pending one. Pass `--no-resume`
  for the old behavior.
- `done` on a task with unmet prerequisites now exits 1 instead of writing.
- Schema: `agent_config.tiers` gained `flagship`, which the `agent` enum already
  allowed; `changelog` and `changelog_entries` descriptions now match what the
  code does; `prerequisites` documents that only `done` satisfies a dependency.
- Examples dropped the redundant `_phase_id` field and use `...Z` timestamps.
- Docs across `README.md`, `docs/philosophy.md`, `docs/integration-guide.md`,
  `docs/memory.md`, and the Hermes skill now describe the actual CLI. The
  stale "Project Structure" block, the "Python 3 standard library only" claim
  (it needs `jsonschema`), and the promised markdown→JSON `convert` are gone —
  markdown→JSON is now documented as a deliberate manual migration.

## [0.1.0] — 2026-09-02

### Added

- `pyproject.toml` — installable via `pip install -e .`; pulls `jsonschema`
  and registers a `taskerkeeper` console command.
- `taskerkeeper/` package — CLI logic lives at `taskerkeeper/cli.py`;
  `scripts/taskerkeeper.py` remains as a shim.
- `docs/memory.md` — project state and decisions for agents.
- `CHANGELOG.md` — this file.

### Fixed

- CLI crashed with `UnicodeEncodeError` on Windows (cp1252 console) printing
  checkmark glyphs. stdout/stderr now reconfigured to UTF-8 with
  `errors="replace"`; files are read/written as UTF-8.
- Schema rejected task `changelog: null`. The field now allows
  `["string", "null"]`, so the zoidmatter-v7 example validates.
- Docs aligned with the actual CLI — removed references to unimplemented
  `status`, `convert-md`, `convert-json`, and `add-task` commands and the
  positional-phase `add` form.

### Changed

- First verified state: both example files pass schema validation, and
  `validate` / `next` / `done` / `add` are exercised end-to-end against both.
