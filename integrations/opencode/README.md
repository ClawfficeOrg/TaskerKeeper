# TaskerKeeper sidebar for OpenCode TUI

Read-only sidebar block driven by the shared core (`taskerkeeper sidebar --json`).
All scheduling logic lives in `taskerkeeper/sidebar.py`; this plugin only shells
out and renders. It never writes to the todo file.

## Install

Add the tuple form to `tui.json` (options object is the second element):

```json
{
  "$schema": "https://opencode.ai/tui.json",
  "plugin": [
    ["taskerkeeper-sidebar", { "todoFile": "docs/todo.json" }]
  ]
}
```

Local path form (this repo):

```json
{
  "plugin": [
    ["./integrations/opencode/tk-sidebar.ts", { "todoFile": "docs/todo-sidebars.json" }]
  ]
}
```

Restart opencode after editing `tui.json` — opencode loads plugin config once
at startup, so edits only take effect on the next launch.

## Options

| Key        | Default                        | Meaning                                          |
| ---------- | ------------------------------ | ------------------------------------------------ |
| `todoFile` | `$TASKERKEEPER_TODO`, else `docs/todo-sidebars.json` under the session directory | Which todo file the sidebar reads |
| `width`    | `60`                           | Forwarded to `sidebar --width` (line truncation) |
| `height`   | `40`                           | Forwarded to `sidebar --height` (row budgets)    |
| `order`    | `400`                          | Slot order (internal blocks use 100–500)         |
| `collapsed`| `false`                        | `true` renders header + counts only              |

## What it renders

Collapsible `TaskerKeeper ▼` block with the same five sections as the core,
polled fresh on every render via `execFileSync("taskerkeeper", ["sidebar",
<todo>, "--json", ...])` with a 3s timeout:

1. **Current** — in-progress tasks with owner, claim age, goal, touches
2. **Concurrent** — disjoint fan-out set, plus deferred-by-overlap count
3. **Upcoming** — ready tasks plus top blocked with `[waits: …]`
4. **Phase tree** — current phase, cut to room with `…N more`
5. **Overall** — per-phase done/total, cut to room with `…N more`

The plugin first tries the `taskerkeeper` console script, then falls back to
`python -m taskerkeeper` (covers boxes whose installed script predates the
`sidebar` command). Either way it is the same shared core — no DAG logic here.

On any CLI error (missing binary, bad todo path, bad JSON) it renders the
one-liner `TaskerKeeper unavailable` and never throws.

## Verify without opencode

```powershell
taskerkeeper sidebar docs/todo-sidebars.json --json --width 60 --height 40
npx -y typescript@5.6.3 tsc --noEmit --strict --skipLibCheck --target es2022 --module nodenext integrations/opencode/tk-sidebar.ts
```
