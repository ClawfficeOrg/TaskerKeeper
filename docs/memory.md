# TaskerKeeper Memory

Project state and decisions for agents. Read before starting work. Rules for
working on this repo live in `AGENTS.md`.

## What This Is

Structured JSON task management for autonomous agents. Alternative to markdown
todo files. Stable IDs, dependency DAG, parallel execution groups, semver
version mapping. Built for [ralph](https://github.com/ClawfficeOrg/Zoid), works
with any autonomous agent. See `docs/philosophy.md` for rationale.

## Layout

```
taskerkeeper/cli.py                      CLI logic (the only implementation)
taskerkeeper/__main__.py                 python -m taskerkeeper
taskerkeeper/schema/todo-v1.schema.json  JSON Schema (draft 2020-12), package data
scripts/taskerkeeper.py                  Thin shim -> taskerkeeper.cli.main
ralph/ralph-json.sh                      Thin wrapper -> taskerkeeper CLI
skills/taskerkeeper/SKILL.md             Hermes skill
tests/test_cli.py                        stdlib unittest suite
.github/workflows/ci.yml                 Tests + example validation, Linux/Windows
docs/                                    philosophy, integration guide, memory, notes
examples/                                simple-project, zoidmatter-v7
pyproject.toml                           setuptools packaging, console script
```

## Current State (2026-09-08, v0.2.0)

- v0.1 scaffold (Aug 27 2026) → hardened (Sep 2) → v0.2.0 rework (Sep 8),
  which fixed everything raised in the `docs/notes.md` review.
- Schema now ships as package data, so `pip install .` works, not just
  `pip install -e .`. Verified from site-packages in a clean venv.
- Scheduling is complete: `ready` lists every runnable task, `start` claims one,
  `reset` recovers an orphan, `next` resumes `in_progress` before handing out
  new work. Phase-level prerequisites are enforced, not just task-level.
- `validate` does semantic checks on top of the schema: duplicate IDs, dangling
  task/phase prerequisites, cycles, task ID vs. phase ID mismatch, `moved`
  without `moved_to`, and a warning for prerequisites on `cancelled`/`moved`
  tasks.
- All read commands support `--json`.
- Writes are lock-guarded (`<file>.lock`) and atomic (temp + rename).
- 37 tests, stdlib `unittest`, run on Linux and Windows in CI against a
  non-editable install.

## Decisions

- **One implementation of the DAG.** `ralph/ralph-json.sh` was a parallel
  jq implementation and had already drifted (different ordering, different
  `done` enforcement, a tautological `select`, a `// "none"` that never fired).
  It is now a wrapper that execs the CLI. jq is no longer a dependency.
- **Only `done` satisfies a task prerequisite**, but `cancelled` and `moved`
  count toward *phase* completion. Cancelled work is not coming back, so it
  should not stall a phase; but silently treating it as a satisfied dependency
  would hide a real planning error, so `validate` warns instead.
- **`done` refuses unmet prerequisites without `--force`.** The old jq path
  warned and wrote anyway; the Python path did not check at all. Refusing is the
  safer default now that both go through one code path.
- **Changelog collection is real.** `done --changelog TEXT` appends to the
  phase's `release.changelog_entries`, or to the last phase that has a `release`
  object. The schema used to claim this happened and nothing did it.
- **Changelog is nullable.** Task `changelog` allows `null` = "no entry".
  The zoidmatter-v7 example (task 7.1.2, the merge task) relies on this.
- **Files read/written as UTF-8** (`encoding="utf-8"`, `ensure_ascii=False`).
  Timestamps are written as `...Z` to match the examples.
- **CLI glyphs are intentional** (`✓ ✗ ► · → ═ ─ •`). The UTF-8 reconfigure at
  import makes them safe on Windows cp1252 consoles. Keep both.
- **`convert` is one-way** (JSON → markdown). Markdown → JSON is a manual
  migration; see `docs/philosophy.md`.

## Known Gaps / Next

- The file lock is cooperative and process-local in effect: a crashed process
  can leave `<file>.lock` behind, and it must be deleted by hand. There is no
  stale-lock timeout.
- `agent_config.tiers` is schema-only — nothing in the CLI reads it to pick a
  tier for a task.
- Markdown → JSON conversion is not implemented, by decision rather than
  omission.
- No GitHub Issues sync (see `docs/philosophy.md` "Future Directions").
- `docs/notes.md` is the external v0.1 review that drove the v0.2 rework. It is
  history now; its findings are closed.
