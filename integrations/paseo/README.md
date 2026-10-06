# paseo-taskerkeeper

Read-only Paseo plugin for TaskerKeeper. The nav badge shows the
in-progress count; the workspace tab renders the five `taskerkeeper
sidebar` sections (current with details, concurrent, upcoming with
waits, current-phase tree, overall tree). All scheduling stays in
`taskerkeeper/cli.py` — the server shells out to the CLI.

## Files

- `package.json` — plugin manifest (`paseo.client` / `paseo.server` entries
  plus `settings.todo`, `settings.width`, `settings.height`).
- `server.js` — `handle({ method, params }, ctx)` RPC. The only method is
  `taskerkeeper.sidebar`, which spawns
  `taskerkeeper sidebar <todo> --json --width --height` and returns the
  parsed payload. Read-only, fail-soft (`{ ok: false, error }`).
- `client.js` — `activate(ctx)` registering the `addSidebarItem` nav entry
  and the `addWorkspacePanel` tab via the `@getpaseo/client` SDK shape.
  Polls the RPC every 15s; failures render inline, never throw.

## Install / load

1. Copy (or symlink) this folder to your Paseo plugins directory, or add it
   via the Paseo plugin manager pointing at this path.
2. Enable the `paseo-taskerkeeper` plugin and restart Paseo.
3. The "TaskerKeeper" nav entry and workspace tab appear across Paseo
   clients (desktop and web share the same client/server split).

Requires the `taskerkeeper` CLI on the server's `PATH`.

## Settings (todo path)

- `todo` — path to the todo JSON file the sidebar reads.
  Falls back to the `TASKERKEEPER_TODO` env var.
  Example: `/data/todos/sidebars.json` (or `docs/todo-sidebars.json` in
  this repo).
- `width` (default 80) — passed as `--width`, truncates long lines.
- `height` (default 40) — passed as `--height`, caps section row counts
  (overflow shows as "…N more").

## Screenshots (description)

- Nav: "TaskerKeeper" entry with a numeric badge equal to the
  `current.tasks` length (blank when zero).
- Panel tab "TaskerKeeper": five stacked sections — Current (icon, id,
  title, `[owner age]` line plus goal and `touches:` basenames),
  Concurrent ("safe to fan out" list), Upcoming (pending rows show
  `[waits: ...]`), `Phase <id>: <title>` tree, Overall phase tree —
  each with an "…N more" line when the height budget cuts rows.
- Misconfigured (no todo path) or CLI failure: panel shows a single
  dim line, e.g. `TaskerKeeper: no todo file configured …`.

## Manual load test (paseo plugin scaffold shape)

1. `node --check server.js && node --check client.js`
2. Set `TASKERKEEPER_TODO=<repo>/docs/todo-sidebars.json`, then from node:
   `await require('./server.js').handle({ method: 'taskerkeeper.sidebar',
   params: {} }, {})` → `{ ok: true, payload: { current, concurrent,
   upcoming, phase, overall } }`.
3. Load the plugin in Paseo and open the TaskerKeeper tab: all five
   sections render; badge matches `current.tasks` length.
