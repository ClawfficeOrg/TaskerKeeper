# paseo-tidetable

Read-only Paseo plugin for TideTable (Paseo ≥ 0.11). A sidebar row shows the
in-progress count; its screen, and a workspace panel, render the five
`tidetable sidebar` sections (current with details, concurrent, upcoming with
waits, current-phase tree, overall tree) plus the live TKRun line. All
scheduling stays in `tidetable/cli.py`: the server half shells out to the CLI.

## Layout

Paseo compiles `client/` into the app bundle, `server/` into the daemon
subprocess, and `shared/` into both; a module at the root is a compile error.

- `paseo-plugin.json` — manifest: `id: paseo-tidetable`, `requirements.paseo`.
- `index.client.tsx` — registers the `tidetable` screen, the `tidetable`
  sidebar header item (badge = `current.tasks` length, `►` prefix while a run
  is live), the `tidetable` workspace panel, an "Open TideTable" Command Center
  item, and the settings screen.
- `index.server.ts` — registers the settings document and handles the RPC.
- `shared/sidebar.ts` — `tidetable.sidebar` RPC contract and the payload
  schema (loose, so a newer CLI adding fields still validates).
- `shared/settings.ts` — host settings: `todo`, `server`, `token`, `width`,
  `height`.
- `server/sidebar.ts` — spawns
  `tidetable [--local] sidebar <todo> --json --width --height`, falling back to
  `python -m tidetable` when the CLI is missing or predates `sidebar`.
  Fail-soft: errors come back as `{ ok: false, error }` and render in the panel.
- `client/` — React Native views (`View`/`Text`, theme tokens, no DOM). The
  sidebar row, screen and panel share one 15 s React Query poll.

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

Settings → Plugins → paseo-tidetable → TideTable. Each field falls back to an
environment variable on the daemon.

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

- Sidebar: a "TideTable" row with a list icon; the trailing badge equals the
  number of Current tasks (blank at zero, `►N` while a run is live).
- Pressing it opens the TideTable screen: Run (when present), Current,
  Concurrent, Upcoming (`[waits: …]` on blocked rows), `Phase <id>: <title>`,
  Overall, each with "…N more" when the height budget cuts rows.
- No todo configured, or a CLI error: one muted line,
  `TideTable: no todo file configured …`, in the screen and panel.
