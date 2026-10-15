# paseo-tidetable

Read-only Paseo plugin for TideTable (Paseo ≥ 0.11). A **right-sidebar
(Explorer) panel** follows the open workspace: it finds the TideTable todo
files in that project's directory and renders the five `tidetable sidebar`
sections (current with details, concurrent, upcoming with waits, current-phase
tree, overall tree) plus the live TKRun line. All scheduling stays in
`tidetable/cli.py`: the server half shells out to the CLI.

## Layout

Paseo compiles `client/` into the app bundle, `server/` into the daemon
subprocess, and `shared/` into both; a module at the root is a compile error.

- `paseo-plugin.json` — manifest: `id: paseo-tidetable`, `requirements.paseo`.
- `index.client.tsx` — registers the `tidetable` panel (Explorer, also
  openable as a workspace tab), the `tidetable` screen for the configured todo
  or hub project, two Command Center items ("Show TideTable for this project",
  "Open TideTable (configured todo or hub)"), and the settings screen.
- `index.server.ts` — registers the settings document and handles both RPCs.
- `shared/sidebar.ts` — `tidetable.sidebar` RPC contract (`{ todo? }`: a file
  to read locally, or the settings when omitted) and the payload schema
  (loose, so a newer CLI adding fields still validates).
- `shared/discover.ts` + `server/discover.ts` — `tidetable.discover`: scans a
  workspace directory (depth 4, skipping dot-dirs, `node_modules`, `target`,
  `dist`, `build`, …, at most 5,000 entries) for JSON files with the todo
  schema's required `schema_version`, `project` and `phases`, newest first.
- `shared/settings.ts` — host settings: `todo`, `server`, `token`, `width`,
  `height`.
- `server/sidebar.ts` — spawns
  `tidetable [--local] sidebar <todo> --json --width --height`, falling back to
  `python -m tidetable` when the CLI is missing or predates `sidebar`.
  Fail-soft: errors come back as `{ ok: false, error }` and render in the panel.
- `client/` — React Native views (`View`/`Text`, theme tokens, no DOM). Each
  todo file has one 15 s React Query poll shared by every view of it; the
  workspace rescan runs every 60 s.

## Install

```bash
tidetable integrations install paseo          # runs the line below
paseo plugin install /path/to/TideTable/integrations/paseo
```

Paseo reads a directory source in place, so after `git pull` run
`paseo plugin reload paseo-tidetable`. The desktop app ships the CLI without
putting it on `PATH` (Windows:
`%LOCALAPPDATA%\Programs\Paseo\resources\bin\paseo.cmd`); the integrations
command looks there too. Needs the `tidetable` CLI (≥ 0.10 for hub mode) on
the daemon's `PATH`.

## Settings

The Explorer panel needs no settings: it reads the files it finds, always
locally (`--local`), whatever hub is configured. With several todos in one
project, chips at the top pick one (newest first by default).

The settings feed the standalone screen (Command Center → "Open TideTable
(configured todo or hub)"). Settings → Plugins → paseo-tidetable → TideTable;
each field falls back to an environment variable on the daemon.

- **Todo** — todo file path on the daemon host (`TIDETABLE_TODO`), or the
  project slug in hub mode.
- **Hub URL** — a `tidetable serve` base URL (`TIDETABLE_SERVER`). A hub set
  here always wins; one only inherited from the environment is ignored when
  Todo is an existing file, so a path is never sent to a hub as a slug.
- **Hub token** — bearer token (`TIDETABLE_TOKEN`). The settings document is
  plain host-side JSON every connected client can read, so prefer the env var.
  The token reaches the CLI through its environment, never argv.
- **Width** / **Height** (80 / 40) — `--width` truncates lines, `--height`
  caps section rows (overflow shows as "…N more").

## Develop

```bash
npm install            # dev-only: SDK types, React Native, TypeScript
npm run typecheck
paseo plugin reload paseo-tidetable
paseo plugin logs paseo-tidetable
```

Paseo supplies `@getpaseo/plugin`, React, React Native, React Query and zod
at runtime; `node_modules/` is for typechecking only.

## What to look at after a change

- Right sidebar: a "TideTable" tab with a list icon in the Explorer. In a
  project with todo files it shows the project name, then Run (when present),
  Current, Concurrent, Upcoming (`[waits: …]` on blocked rows),
  `Phase <id>: <title>` and Overall, each with "…N more" when the height budget
  cuts rows. Several todos: a chip per file (`docs/todo-v1.json`, …) above.
- A project with no todos: "No TideTable todo files in this project."
- Switching workspaces switches the panel to that project's todos.
