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
taskerkeeper/agents.py                   Tier -> provider/model config layering
taskerkeeper/jsonio.py                   Atomic writes, the file lock, the event log
taskerkeeper/__main__.py                 python -m taskerkeeper
taskerkeeper/schema/todo-v1.schema.json  JSON Schema (draft 2020-12), package data
scripts/taskerkeeper.py                  Thin shim -> taskerkeeper.cli.main
ralph/ralph-json.sh                      Thin wrapper -> taskerkeeper CLI
skills/taskerkeeper/SKILL.md             Hermes skill
tests/test_cli.py                        stdlib unittest suite
.github/workflows/ci.yml                 Tests + example validation, Linux/Windows
docs/                                    philosophy, integration guide, memory,
                                         module-plan, notes
examples/                                simple-project, zoidmatter-v7
pyproject.toml                           setuptools packaging, console script
```

## Current State (2026-09-08, v0.5.0)

- v0.1 scaffold (Aug 27 2026) → hardened (Sep 2) → v0.2.0 rework (Sep 8),
  which fixed everything raised in the `docs/notes.md` review → v0.3.0 (Sep 8),
  which added the agent provider/model config → v0.4.0 (Sep 8), provider presets.
- Schema now ships as package data, so `pip install .` works, not just
  `pip install -e .`. Verified from site-packages in a clean venv.
- Scheduling is complete: `ready` lists every runnable task, `start` claims one,
  `reset` recovers an orphan, `next` resumes `in_progress` before handing out
  new work. Phase-level prerequisites are enforced, not just task-level.
- `validate` does semantic checks on top of the schema: duplicate IDs, dangling
  task/phase prerequisites, cycles, task ID vs. phase ID mismatch, `moved`
  without `moved_to`, and a warning for prerequisites on `cancelled`/`moved`
  tasks.
- All read commands support `--json`, and task output carries the resolved
  provider/model (v0.3.0).
- Writes are lock-guarded (`<file>.lock`) and atomic (temp + rename). The lock
  records pid + host + time, and a lock whose holder is confirmed gone (or which
  has aged past `stale_after`, default 300s) is broken automatically.
- v0.5.0 made parallel execution real rather than advertised: claims with
  leases, `--disjoint` fan-out from `touches`, an append-only event log,
  complexity-driven tier selection, and an explicit `release` command.
- 107 tests, stdlib `unittest`, run on Linux and Windows in CI against a
  non-editable install.

## Parallel Execution (v0.5.0)

- **Claims.** `start` writes `claimed_by` / `claimed_at` / `lease_expires_at`;
  any non-`in_progress` status clears them. `start` refuses a live claim held by
  someone else, `done` needs `--force` for one, and `find_next` returns an
  `in_progress` task only when `claimable_by` accepts it. Owner comes from
  `--owner`, else `TASKERKEEPER_OWNER`, else `host:pid`; lease from `--lease`,
  else `TASKERKEEPER_LEASE_MINUTES`, else 60 minutes.
- **Disjoint fan-out.** `ready --disjoint` / `next --disjoint` filter by
  `touches` overlap (`paths_conflict`: equal, or one a directory containing the
  other), counting in-progress tasks as holding their paths. Greedy in ID order
  so the result is stable. Plain `ready` reports the overlaps as a warning.
- **Event log.** `<file>.events.jsonl`, one JSON line per transition, written
  inside the lock, best-effort. `history` replays it. `TASKERKEEPER_EVENTS=0`
  disables.
- **Complexity → tier.** A task with `complexity` and no `agent` gets a tier:
  Low→basic, Medium→mid, High→pro, Very High→flagship, overridable by a tier's
  `complexity_range` in any config layer. `resolve_task` reports the tier it
  used and whether it was derived.
- **Releases.** `done --json` reports `release_ready`; `release [phase]` prints
  what would be tagged and `release --tag` creates the annotated tag from the
  release notes plus the collected changelog.

## Agent Model Config (v0.3.0, presets in v0.4.0)

- A task names a tier (`task.agent`); `taskerkeeper/agents.py` resolves that
  tier to a provider and model, and `next` / `ready` / `parallel --json` carry
  the resolved values so a supervisor dispatches in one lookup.
- Layers, last wins: built-in `anthropic` preset -> user config -> repo config
  -> the todo file's `agent_config` -> a task's own `provider`/`model`.
- Each config layer contributes twice: the preset it names via `provider`, then
  its own `tiers` entries. So `agents use` moves a whole scope and `agents set`
  in that scope still wins.
- `PROVIDER_PRESETS` ships `anthropic` and `opencode-go` (glm-5.3-flash for
  basic/mid, deepseek-v4-pro for pro, qwen3.8-max for flagship). Adding a
  provider is a dict entry plus a README row.
- `agents show` reports which layer supplied each setting, and which preset is
  active. `agents providers` lists the presets.
- `TASKERKEEPER_CONFIG_HOME` relocates the user config; the tests rely on it.

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
- **`parallel_group` schedules nothing.** It was documented as making groups run
  sequentially and no code ever did that. Ordering is `prerequisites`;
  concurrency safety is `touches`. Two gating mechanisms would mean two ways for
  a task to be mysteriously blocked.
- **Tagging is explicit.** `done` reports `release_ready` rather than creating a
  tag, because the phase's last task is an ordinary task and a git side effect
  fired from it lands unpredictably.
- **The event log is a sibling file, not a field.** Appending is one syscall,
  the todo file stays diffable, and a crash mid-append costs one line instead of
  the roadmap.
- **Pid liveness is never checked with `os.kill(pid, 0)`.** On Windows that
  terminates the process. `jsonio._pid_alive` uses the Win32 API and returns
  `None` when unsure; unknown liveness falls back to lock age.
- **Model config is per machine by default, not per roadmap.** Which model runs
  a tier is a property of whoever runs the agents, so the user layer is its
  normal home. The repo and todo layers exist for projects that genuinely need
  to pin something, not as the default place to put it.
- **TaskerKeeper never calls a provider.** It resolves strings and hands them
  over: no SDK dependency, no API keys, no network. Unknown settings
  (`--option effort=xhigh`) pass through untouched so a supervisor can use them
  without TaskerKeeper knowing what they mean.

## Known Gaps / Next

- The lock is still cooperative. Stale locks now break themselves, but two
  processes racing on a filesystem without atomic `O_EXCL` (some network mounts)
  are still not protected.
- A lease is not renewed while a task runs, so a task genuinely longer than the
  lease can be stolen mid-flight. Re-running `start` as the same owner refreshes
  it; a supervisor with long tasks should either do that periodically or raise
  `TASKERKEEPER_LEASE_MINUTES`.
- `touches` conflict detection is textual. It does not know that two tasks
  editing different functions in one file might be fine, and it cannot see a
  file a task forgot to declare.
- The event log has no rotation or compaction.
- `cli.py` still fuses scheduling logic, rendering, and argparse. Splitting it
  into an importable core is the next structural change — see
  `docs/module-plan.md`.
- Not published to PyPI; consumers clone. Also in `docs/module-plan.md`.
- Markdown → JSON conversion is not implemented, by decision rather than
  omission.
- No GitHub Issues sync (see `docs/philosophy.md` "Future Directions").
- `docs/notes.md` is the external v0.1 review that drove the v0.2 rework. It is
  history now; its findings are closed.
