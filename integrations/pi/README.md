# pi-taskerkeeper-sidebar

TaskerKeeper sidebar panel for [pi coding agent](https://github.com/earendil-works/pi).
Same extension also loads under [oh-my-pi](https://github.com/can1357/oh-my-pi) (`omp`),
which shares the Pi extension API and accepts the legacy `pi` manifest key.

Renders the five shared sections — Current, Concurrent, Upcoming, phase tree,
overall tree — from `taskerkeeper sidebar <todo> --json`. All scheduling logic
lives in `taskerkeeper/sidebar.py`; this package shells out and renders only.
Read-only: it never writes the todo file.

## Install

Local path (dev):

```bash
pi install ./integrations/pi
```

From npm (after publish):

```bash
pi install npm:pi-taskerkeeper-sidebar
```

The extension auto-registers via the `"pi"` field in `package.json`:

```json
{ "pi": { "extensions": ["./index.js"] } }
```

The same file is listed under `"omp"`, so `omp` loads it identically
(`omp.extensions` accepts the same entry; the older `pi.extensions` field
remains compatible).

Manual load for one session:

```bash
pi -e ./integrations/pi/index.js
omp -e ./integrations/pi/index.js
```

Requirements: `taskerkeeper` on PATH (or `python -m taskerkeeper` working),
Node >= 18. No runtime dependencies — node builtins only.

## Commands

```
/tk-sidebar on        # enable panel + refresh
/tk-sidebar off       # disable panel
/tk-sidebar width 45  # set sidebar width (10-120)
/tk-sidebar status    # show on/off, width, todo file, last error
/tk-sidebar           # same as status
```

Panel auto-refreshes on `session_start`, `turn_end`, and `message_end`.

## Configuration

| Source | Default | Purpose |
| --- | --- | --- |
| option `todo` / `$TASKERKEEPER_TODO` | `docs/todo-sidebars.json` | todo file passed to the CLI |
| option `width` / `$TASKERKEEPER_SIDEBAR_WIDTH` | `45` | sidebar line width |
| option `height` / `$TASKERKEEPER_SIDEBAR_HEIGHT` | `40` | row budget sent to the CLI |

## How it works

1. `index.js` factory receives the Pi `ExtensionAPI`, primes a refresh, then
   subscribes to `session_start` / `turn_end` / `message_end`.
2. Each refresh spawns `taskerkeeper sidebar <todo> --json --width N
   --height M` (falls back to `python -m taskerkeeper`), parses the payload,
   and caches rendered lines.
3. `panels/taskerkeeper.js` `renderPanel(payload, width)` formats the five
   sections with owner/age/goal, waits, touches (basenames only), trees, and
   `…N more` counts, truncating every line to width.
4. Missing CLI or bad JSON degrades to one line —
   `TaskerKeeper: unavailable (taskerkeeper CLI not found)` — instead of
   throwing in the TUI.

## Manual test (pi and omp)

1. `pi -e ./integrations/pi/index.js`, then `/tk-sidebar status` shows `on`.
2. `/tk-sidebar width 30`, panel lines stay within 30 columns.
3. Rename the CLI off PATH and trigger a turn: panel shows the single
   unavailable line.
4. Repeat steps 1–3 under `omp -e ./integrations/pi/index.js`; behavior is
   identical because both hosts implement `pi.on` + `pi.registerCommand`.
