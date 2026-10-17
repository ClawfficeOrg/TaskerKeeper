# TideTable sidebar for OpenCode TUI

Read-only sidebar block driven by the shared core (`tidetable sidebar --json`).
All scheduling logic lives in `tidetable/sidebar.py`; this plugin only shells
out and renders. It never writes to the todo file.

## Install

Add the tuple form to `tui.json` (options object is the second element):

```json
{
  "$schema": "https://opencode.ai/tui.json",
  "plugin": [
    ["tidetable-sidebar", { "todoFile": "docs/todo.json" }]
  ]
}
```

Local path form (this repo):

```json
{
  "plugin": [
    ["./integrations/opencode/tk-sidebar.ts", { "todoFile": "docs/todo.json" }]
  ]
}
```

Restart opencode after editing `tui.json` — opencode loads plugin config once
at startup, so edits only take effect on the next launch.

## Options

| Key        | Default                        | Meaning                                          |
| ---------- | ------------------------------ | ------------------------------------------------ |
| `todoFile` | `$TIDETABLE_TODO`, else `docs/todo.json` under the session directory | Which todo file the sidebar reads |
| `width`    | `60`                           | Forwarded to `sidebar --width` (line truncation) |
| `height`   | `40`                           | Forwarded to `sidebar --height` (row budgets)    |
| `order`    | `400`                          | Slot order (internal blocks use 100–500)         |
| `collapsed`| `false`                        | `true` renders header + counts only              |

## What it renders

Collapsible `TideTable ▼` block with the same five sections as the core,
polled fresh on every render via `execFileSync("tidetable", ["sidebar",
<todo>, "--json", ...])` with a 3s timeout:

1. **Current** — in-progress tasks with owner, claim age, goal, touches
2. **Concurrent** — disjoint fan-out set, plus deferred-by-overlap count
3. **Upcoming** — ready tasks plus top blocked with `[waits: …]`
4. **Phase tree** — current phase, cut to room with `…N more`
5. **Overall** — per-phase done/total, cut to room with `…N more`

The plugin first tries the `tidetable` console script, then falls back to
`python -m tidetable` (covers boxes whose installed script predates the
`sidebar` command). Either way it is the same shared core — no DAG logic here.

On any CLI error (missing binary, bad todo path, bad JSON) it renders the
one-liner `TideTable unavailable` and never throws.

## Adjudicator bridge (`tk-adjudicator.ts`)

A second, separate plugin: the unattended-run permission hook. TKRun starts a
loopback decision service and passes its address to the agent; this plugin asks
it, in the `execute.before` hook for `bash`/`shell`, whether the command may run,
and **throws** on a non-allow answer (which aborts the tool). All the logic — the
allow/deny lists, the decision model call, the audit log — lives in
`tidetable/tkrun.py`; this file only bridges.

It is registered automatically by the generated `opencode.json` when
`adjudicator.enabled` is set, so you do not wire it by hand. It stays inert in an
attended session: with no `TIDETABLE_ADJUDICATOR_HOST`/`_PORT` and no
`.tkrun/adjudicator.json`, it registers no hook.

It **fails closed**: no discovery, a dead service, a timeout, or any non-`allow`
verdict all throw. It never sees the decision-model API key — only `host:port`.

## Verify without opencode

```powershell
tidetable sidebar docs/todo.json --json --width 60 --height 40
npx -y typescript@5.6.3 tsc --noEmit --strict --skipLibCheck --target es2022 --module nodenext integrations/opencode/tk-sidebar.ts
```
