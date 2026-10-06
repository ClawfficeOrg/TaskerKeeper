# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`taskerkeeper integrations list|install|uninstall`.** Installer for the
  harness sidebars: symlinks the Paseo plugin (copy fallback), merges the
  opencode `tui.json` plugin entry (refuses non-JSON, keeps a `.bak`),
  runs `pi install`, and prints the OpenChamber folder to paste. Run from a
  checkout (`integrations/` is not packaged); `--dry-run` previews.
- **`taskerkeeper overnight run|init|stop`.** A repo-agnostic unattended
  runner, ported from a per-repo PowerShell script. Per task: claim, run the
  tier's agent (`claude -p` / `opencode run`) with a generated allow/deny
  list and no skip-permissions flag, run the repo's gate commands itself, read-only
  review, commit, `done`. Sleeps through usage limits, parks failures as stashes,
  stops after two consecutive failures. Repo specifics (gate, prompt rules,
  deny-edit globs, sibling repos) live in `.taskerkeeper/overnight.json`;
  with no config the gate is inferred from Cargo.toml / package.json /
  pyproject.toml / go.mod. See `docs/overnight.md`.

## [0.7.0] — 2026-09-23

Harness sidebars: one budgeted payload, four thin adapters.

### Added

- **`taskerkeeper sidebar <file> [--width] [--height]`.** Emits current
  (in-progress with owner, claim age, goal, touches), concurrent (the
  disjoint fan-out set), upcoming (deferred plus blocked with reasons), the
  current-phase tree anchored on live work, and the overall phase tree.
  Sections cut to room report `more` counts instead of silently dropping
  tasks; touches are basenamed. Text render matches `list` glyphs.
  Formatting only — every scheduling answer delegates to `cli.py`.
- **`integrations/opencode/`.** TUI plugin registering the `sidebar_content`
  slot (opencode-harness-panel pattern): collapsible read-only block, polls
  the sidebar command, fail-soft one-liner when the CLI is missing.
- **`integrations/pi/`.** Extension panel plus `/tk-sidebar on|off|width`
  in pi-sidebar-tui style; declares both `pi` and `omp` manifest keys so it
  loads under oh-my-pi too.
- **`integrations/openchamber/`.** Rail extension (manifest, dependency-free
  IIFE panel, full-page board): reads the serve API, attaches tasks to chat,
  zero capabilities for read-only.
- **`integrations/paseo/`.** Plugin with sidebar item (in-progress badge)
  and workspace panel tab, served through a read-only server RPC.
- **`docs/todo-sidebars.json`.** The milestone roadmap, worked through
  claim/ready/done like the hub before it.

## [0.6.0] — 2026-09-22

The VPS hub milestone: one single-writer core API so remote agents share a
todo file without NFS locking, plus a read-only live dashboard.

### Added

- **`taskerkeeper serve`.** stdlib HTTP API over the same DAG functions in
  `cli.py` — no second implementation. GET reads (`ready`, `next`, `list`,
  `deps`, `history`, `validate`, `parallel`) return the `--json` shapes;
  POST writes (`start`, `done`, `reset`, `status`, `add`) hold a server lock
  plus `FileLock`, reload inside the lock, and enforce claim leases, refusing
  a live foreign claim. Binds `127.0.0.1:8471` by default behind Traefik.
- **Agent presence.** `POST /api/<slug>/heartbeat` records repo, branch,
  worktree, and task in a sqlite sessions store
  (`taskerkeeper/sessions.py`); `start` heartbeats automatically,
  `done`/`reset` clear the task. Rows older than 180s read as stale.
- **Postgres projection.** `deploy/migrations/001_init.sql` defines
  `projects`, `tasks_snap`, `events`, `sessions`, and `machine_tokens`;
  `taskerkeeper/projector.py` tails each todo file and its `.events.jsonl`
  into those tables. Best-effort, never in the write path; Postgres decides
  nothing about scheduling.
- **SSE live stream.** `GET /api/stream?slugs=a,b&since=SEQ` replays merged
  events with `Last-Event-ID` support and a 15s heartbeat comment.
- **Dashboard scaffold (`web/`).** SvelteKit shell with a fleet view, an
  agents wall with staleness display, and a per-project view with blockers,
  disjoint `deferred` warnings, and a live event tail. Read-only; BetterAuth
  OIDC against Authentik as IdP.
- **Machine tokens (`taskerkeeper/tokens.py`).** Mintable per-project tokens
  with scopes and expiry, stored hashed, verified before the shared
  `TK_API_TOKEN` fallback. Revocation is a flag.
- **Deploy topology (`deploy/`).** `registry.json` maps slugs to todo paths;
  `compose.yml` wires core, Postgres, and dashboard with Traefik labels where
  `/api` deliberately bypasses forwardAuth (bearer only).
- **`docs/todo-vps-hub.json`.** The milestone roadmap, built and worked
  entirely through claim/ready/done.

## [0.5.0] — 2026-09-08

Parallel execution was the headline feature and was not actually enforced. This
release makes it real.

### Added

- **Claims with leases.** `start` records `claimed_by`, `claimed_at`, and
  `lease_expires_at` on a task. `start` refuses a task another agent holds under
  a live lease, `next` never hands out a live claim, and `done` needs `--force`
  to finish someone else's. A crashed agent's task becomes claimable when its
  lease lapses, so recovery no longer means editing fields by hand. Owner from
  `--owner` / `TASKERKEEPER_OWNER` / `host:pid`; lease from `--lease` /
  `TASKERKEEPER_LEASE_MINUTES` / 60 minutes.
- **`ready --disjoint` and `next --disjoint`.** `touches` is now scheduling
  input: tasks whose owned paths overlap are never emitted together, and tasks
  already in progress hold their paths. Paths overlap when equal or when one is
  a directory containing the other. Selection is greedy in ID order, so results
  are stable; skipped tasks come back under `deferred`. Plain `ready` reports
  overlaps as a warning instead of silently listing unsafe work.
- **Append-only event log.** Every `start`, `done`, `reset`, `status`, `add`,
  and `release` appends a JSON line to `<file>.events.jsonl`. `taskerkeeper
  history` replays it, with `--task` and `--limit`. `TASKERKEEPER_EVENTS=0`
  disables it. Status fields are overwritten in place, so the todo file alone
  could never answer "how many times was this reset, and by whom".
- **`taskerkeeper release [phase]`.** Prints the tag a completed phase ships as,
  with the changelog `done` collected; `--tag` creates the annotated tag,
  refusing an incomplete phase without `--force` and refusing to clobber an
  existing tag. `done --json` now reports `release_ready`. `tag_on_complete` was
  previously a field nothing acted on.
- **Complexity-driven tier selection.** A task with a `complexity` and no
  `agent` is routed by tier: Low→basic, Medium→mid, High→pro, Very High→
  flagship. A tier's `complexity_range` overrides that, accepting `"High"`,
  `"Low-Medium"`, `"Medium to High"`, `"Low, Very High"`. `complexity_range` was
  documentation nothing read; `add` hardcoded `mid_dev_agent`.
- **Stale lock recovery.** The lock file records pid, host, and time. A lock
  whose holder is confirmed gone is broken immediately; one whose holder cannot
  be checked is broken after `stale_after` (default 300s). Commands say when
  they broke one. Pid liveness on Windows goes through `OpenProcess`, never
  `os.kill(pid, 0)` — which on Windows terminates the process.
- `--json` on `start` and `done`; `--owner` on `next`, `start`, `done`, `reset`,
  and `status`.

### Changed

- **`parallel_group` no longer claims to schedule anything.** The docs said
  "different groups run sequentially within a phase"; no code ever implemented
  it. It is a label, read only by `parallel` and `convert`. Ordering is
  `prerequisites`; concurrency safety is `touches`.
- `resolve_task` now reports the tier it resolved (`agent`) and whether that
  tier was derived (`agent_derived`), so an unknown tier returns
  `{"agent": ..., "agent_derived": false}` rather than `{}`.
- `task_summary` carries the claim fields, so `ready --json` and `next --json`
  show who holds what.

### Fixed

- `next` could hand a live `in_progress` task to a second agent, putting two
  agents on the same work — the exact failure claiming is meant to prevent. It
  now resumes only the caller's own claim or an expired one.
- A crashed process left `<file>.lock` behind forever. The lock recorded a pid
  and nothing ever read it.

### Schema

Additive only. New task fields `claimed_by`, `claimed_at`, `lease_expires_at`;
`agent` documented as optional and derivable; descriptions corrected for
`parallel_group`, `touches`, and `complexity_range`. Existing files validate
unchanged.

## [0.4.0] — 2026-09-08

### Added

- Provider presets — a whole tier table per provider, so switching providers is
  one command instead of four.

  | Tier | `anthropic` | `opencode-go` |
  |------|-------------|---------------|
  | `basic_dev_agent` | `claude-haiku-4-5` | `glm-5.3-flash` |
  | `mid_dev_agent` | `claude-sonnet-5` | `glm-5.3-flash` |
  | `pro_dev_agent` | `claude-opus-5` | `deepseek-v4-pro` |
  | `flagship` | `claude-fable-5-1` | `qwen3.8-max` |

- `agents use <provider>` points a scope at a preset; `agents use --clear` drops
  a scope's selection. `agents providers` lists the presets and marks the active
  one. Both honour `--scope user` (default) / `repo` / `todo`.
- `agents show` reports the active preset and the layer that selected it, and
  labels preset-supplied settings as `<scope> preset` in the source column.
  `--json` gains `provider` and `provider_source`.
- Schema: `agent_config.provider` selects a preset for one todo file.

### Changed

- Each config layer now contributes twice — the preset it names, then its own
  `tiers` entries — so `agents set` still wins over `agents use` in the same
  scope, while a higher scope's preset wins over a lower scope's tier.
- `agents.read_config()` returns the whole config file; the old tiers-only
  behavior is `agents.read_tiers()`. `DEFAULT_TIERS` is now
  `PROVIDER_PRESETS["anthropic"]` and keeps working.

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
