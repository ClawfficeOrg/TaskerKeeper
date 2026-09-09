# Module Plan — TaskerKeeper as a dependency

**Status:** plan, not yet implemented (as of v0.5.0)
**Goal:** a project can add TaskerKeeper as a dependency and drive it from
Python, instead of cloning the repo and shelling out to a CLI.

TaskerKeeper works today, but only as a program. Everything a consumer needs —
the DAG, the claim rules, the conflict detection — is fused into `cli.py`
alongside argparse and box-drawing output, and the package is not published, so
"reusable module" currently means "clone it and parse `--json`". This is the
plan to fix that, in the order the steps should land.

Nothing here changes scheduling semantics. If a step would, it does not belong
in this plan.

---

## 1. Split `cli.py` into core, render, and CLI

`taskerkeeper/cli.py` is ~1500 lines holding three unrelated jobs. A consumer
importing it today gets argparse and a UTF-8 stdout reconfigure as side effects,
and every function that decides something also prints something.

Target layout:

```
taskerkeeper/model.py    Pure logic. Load, validate, schedule, claim, conflict.
                         No printing, no argparse, no sys.exit, no subprocess.
taskerkeeper/render.py   Human output: glyphs, tables, the markdown converter.
taskerkeeper/cli.py      argparse, command handlers, exit codes. Calls the above.
taskerkeeper/jsonio.py   Unchanged — already a clean seam.
taskerkeeper/agents.py   Unchanged — already a clean seam.
```

What moves to `model.py`, roughly in dependency order:

- traversal: `iter_tasks`, `all_tasks`, `find_task`, `id_key`
- scheduling: `blockers_for`, `ready_tasks`, `in_progress_tasks`, `find_next`,
  `blocked_report`, `unblocked_by`, `phase_is_complete`, `complete_phase_ids`
- claims: `claim_task`, `release_claim`, `claimable_by`, `lease_expired`,
  `claim_summary`, `default_owner`, `default_lease_minutes`, `parse_ts`
- conflicts: `norm_path`, `paths_conflict`, `overlapping_paths`,
  `touch_conflicts`, `disjoint_tasks`
- validation: `semantic_errors`, `find_cycles`
- mutation: `set_status`, `add_task`, `collect_changelog`, `next_task_id`
- release: `release_state`, `releasing_phases`, `tag_message`

What stays in `cli.py`: every `cmd_*` handler, `build_parser`, `main`, the
`subprocess` call in `release --tag`, and the stdout reconfigure.

Constraints:

- **`validate_todo` splits.** The decision half (`semantic_errors` plus schema
  check) goes to `model.py` and returns findings; the printing half stays in
  `cli.py`. Nothing in `model.py` prints.
- **Re-export for compatibility.** `cli.py` keeps `from taskerkeeper.model
  import *`-style explicit re-exports for one minor release, because
  `tests/test_cli.py` and any existing consumer reference `cli.paths_conflict`,
  `cli.FileLock`, and friends. Deprecate in the release after.
- **Tests split with the code.** `tests/test_model.py` for the pure logic —
  which stops needing `TempTodo` and `redirect_stdout` for most cases — and
  `tests/test_cli.py` for exit codes, output, and argument parsing.

Do this first. Every later step is easier once there is a module to publish, a
surface to document, and functions that return instead of print.

## 2. Define and document exit codes

Autonomous loops branch on exit status, so it is contract surface, and right
now it is accidental: `ready` and `next` return `1` both for "nothing to do" and
for "the file is broken", which a supervisor cannot tell apart. `LockTimeout`
also returns `1`.

```python
EXIT_OK = 0             # the command did what it says
EXIT_ERROR = 1          # bad input, unknown id, refused mutation, invalid file
EXIT_NOTHING_READY = 2  # valid request, no work: ready/next/history empty,
                        # release on an incomplete phase
EXIT_LOCKED = 3         # LockTimeout: someone else holds the file
```

This is a **breaking change** for any caller that treats non-zero as fatal, so
it ships in its own release with a loud CHANGELOG entry, and
`ralph/ralph-json.sh` is checked against it in the same change. Add a table to
`README.md` and a test per code.

## 3. Publish to PyPI

`pip install taskerkeeper` should work. Today `README.md` says `git clone`,
which makes the tool un-dependable in the literal sense.

- Reserve the name; confirm no collision (the `rtk` precedent in this ecosystem
  is a warning).
- Fill in `pyproject.toml` metadata: `description`, `readme`, `license`,
  `authors`, `urls` (Homepage/Changelog/Issues), classifiers,
  `requires-python = ">=3.10"`.
- Verify the schema still ships as package data from the built wheel, not just
  the sdist — `pip install dist/*.whl` in a clean venv, then `taskerkeeper
  validate` from a directory outside the checkout. This is the failure mode
  v0.2 already hit once.
- Add a `release` job to `.github/workflows/`: build with `python -m build`,
  publish with trusted publishing on a tag, after the test matrix passes.
- Replace the `git clone` block in `README.md` with `pip install taskerkeeper`,
  keeping the checkout instructions under a "from source" heading.

## 4. Commit to a public Python API

Once `model.py` exists, decide what is supported and say so in
`taskerkeeper/__init__.py`, so consumers stop importing from `cli`:

```python
from taskerkeeper import (
    load_todo, validate,            # read + check
    ready_tasks, find_next, disjoint_tasks, blockers_for,   # schedule
    start_task, finish_task, reset_task,                     # mutate (locked)
    resolve_task,                                            # tier -> model
)
```

Two things need designing rather than just moving:

- **Mutations need a locked, importable form.** Today the lock, reload, mutate,
  save, log sequence lives inside each `cmd_*`. A consumer calling
  `set_status()` directly gets none of it. Extract one helper —
  `with edit_todo(path) as data:` — that takes the lock, reloads inside it,
  yields, saves atomically, and appends the event, and have both the CLI and the
  public API go through it.
- **Errors become exceptions.** `cmd_*` returns an int and prints; the API
  should raise `TaskNotFound`, `PrerequisitesUnmet`, `ClaimHeld`, `LockTimeout`.
  The CLI catches them and maps to exit codes and messages. One error taxonomy,
  two presentations.

Everything not in `__all__` is internal and may change without a major bump.
State that in `AGENTS.md`.

## 5. Nice-to-haves, explicitly deferred

Listed so they are not mistaken for oversights:

- **Lease renewal.** A long task can currently be stolen mid-flight; re-running
  `start` as the same owner refreshes it. A `taskerkeeper heartbeat <id>` would
  be cleaner but adds a daemon-shaped concern to a file-based tool.
- **Event log rotation.** Nothing compacts `<file>.events.jsonl`.
- **Markdown → JSON conversion.** Still a deliberate omission
  (`docs/philosophy.md`), though the mechanical fields parse cleanly and only
  prerequisites need judgement — worth revisiting if a real migration stalls on
  it.
- **Multi-file roadmaps.** One file, one lock. Splitting roadmaps across files
  needs a lock-ordering story before anything else.

---

## Order and why

1. **Split** — nothing else is clean until logic and printing are separable.
2. **Exit codes** — small, breaking, best done before there are PyPI users.
3. **Publish** — safe once the surface is stable.
4. **Public API** — commit to it only after the split has shaken out.

Steps 1 and 2 are internal and can land in any release. Step 3 is the point of
no return on naming and packaging. Step 4 is a promise, so it goes last.
