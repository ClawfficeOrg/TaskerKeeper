"use strict";

/**
 * pi-taskerkeeper-sidebar — TaskerKeeper sidebar panel for pi (and oh-my-pi).
 *
 * pi-sidebar-tui style: package manifest registers this entry via the "pi"
 * field (plus "omp" for oh-my-pi, which accepts the legacy pi key), the
 * factory hooks session_start / turn_end / message_end to refresh cached
 * state, and /tk-sidebar controls the panel.
 *
 * Scheduling lives in `taskerkeeper sidebar <todo> --json`
 * (taskerkeeper/sidebar.py). This file shells out and renders only.
 * Read-only: never writes the todo file. Fail-soft: a missing CLI becomes
 * one unavailable line, never an exception in the TUI.
 */

const { spawnSync } = require("node:child_process");
const { renderPanel, unavailableLine } = require("./panels/taskerkeeper");

const DEFAULT_TODO = "docs/todo-sidebars.json";
const DEFAULT_WIDTH = 45;
const DEFAULT_HEIGHT = 40;

function resolveTodo(options) {
  if (options && typeof options.todo === "string" && options.todo) return options.todo;
  const env = process.env.TASKERKEEPER_TODO;
  if (env) return env;
  return DEFAULT_TODO;
}

function resolveWidth(options) {
  const raw =
    (options && options.width) || process.env.TASKERKEEPER_SIDEBAR_WIDTH || DEFAULT_WIDTH;
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n)) return DEFAULT_WIDTH;
  return Math.min(120, Math.max(10, n));
}

function resolveHeight(options) {
  const raw =
    (options && options.height) || process.env.TASKERKEEPER_SIDEBAR_HEIGHT || DEFAULT_HEIGHT;
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n)) return DEFAULT_HEIGHT;
  return Math.min(200, Math.max(10, n));
}

/** Run the CLI once, synchronously. Returns { payload } or { error }. */
function fetchPayload(todo, width, height) {
  const args = ["sidebar", todo, "--json", "--width", String(width), "--height", String(height)];
  // Prefer the installed binary; fall back to `python -m taskerkeeper`
  // so dev checkouts without PATH setup still work.
  const attempts = [
    { cmd: "taskerkeeper", args },
    { cmd: "python", args: ["-m", "taskerkeeper", ...args] },
    { cmd: "python3", args: ["-m", "taskerkeeper", ...args] },
  ];
  let lastError = "taskerkeeper CLI not found";
  for (const a of attempts) {
    try {
      const res = spawnSync(a.cmd, a.args, { encoding: "utf8", timeout: 8000 });
      if (res.error) {
        lastError = String(res.error.message || res.error);
        continue;
      }
      if (res.status !== 0) {
        lastError = String((res.stderr || res.stdout || "exit " + res.status)).slice(0, 300);
        continue;
      }
      try {
        return { payload: JSON.parse(String(res.stdout || "{}")) };
      } catch (parseErr) {
        lastError = "bad sidebar JSON: " + String(parseErr.message || parseErr).slice(0, 200);
        continue;
      }
    } catch (err) {
      lastError = String((err && err.message) || err);
    }
  }
  return { error: lastError };
}

function clampWidth(n) {
  if (!Number.isFinite(n)) return null;
  return Math.min(120, Math.max(10, n));
}

function taskerkeeperSidebarExtension(pi, options) {
  const opts = options && typeof options === "object" ? options : {};
  const state = {
    enabled: true,
    width: resolveWidth(opts),
    height: resolveHeight(opts),
    todo: resolveTodo(opts),
    lines: [unavailableLine()],
    error: "",
  };

  function refresh() {
    if (!state.enabled) return state.lines;
    const { payload, error } = fetchPayload(state.todo, state.width, state.height);
    if (payload) {
      state.error = "";
      state.lines = renderPanel(payload, state.width);
    } else {
      state.error = error || "unknown error";
      state.lines = [unavailableLine()];
    }
    // Expose for sidebar compositors that poll the extension instance.
    try {
      pi.taskerkeeperSidebar = {
        lines: state.lines,
        enabled: state.enabled,
        width: state.width,
        todo: state.todo,
        error: state.error,
      };
    } catch (_err) {
      // Host object frozen — cache stays local, panel still serves getLines().
    }
    return state.lines;
  }

  function parseTkArgs(raw) {
    const parts = String(raw || "").trim().split(/\s+/).filter(Boolean);
    return parts;
  }

  async function handleTkSidebar(args, ctx) {
    const parts = parseTkArgs(args);
    const sub = (parts[0] || "").toLowerCase();
    const notify = (msg, kind) => {
      try {
        if (ctx && ctx.ui && typeof ctx.ui.notify === "function") ctx.ui.notify(msg, kind || "info");
      } catch (_err) {
        // Headless / RPC mode: notifying is best-effort.
      }
    };
    if (!sub || sub === "status") {
      refresh();
      notify(
        "TaskerKeeper sidebar " +
          (state.enabled ? "on" : "off") +
          " · width " +
          state.width +
          " · " +
          state.todo +
          (state.error ? " · " + state.error : "")
      );
      return;
    }
    if (sub === "on") {
      state.enabled = true;
      refresh();
      notify("TaskerKeeper sidebar on");
      return;
    }
    if (sub === "off") {
      state.enabled = false;
      notify("TaskerKeeper sidebar off");
      return;
    }
    if (sub === "width" || sub === "w") {
      const n = clampWidth(parseInt(parts[1] || "", 10));
      if (n == null) {
        notify("Usage: /tk-sidebar width N (10-120)", "warning");
        return;
      }
      state.width = n;
      refresh();
      notify("TaskerKeeper sidebar width " + n);
      return;
    }
    if (sub === "toggle" || sub === "toggle-panel") {
      state.enabled = !state.enabled;
      if (state.enabled) refresh();
      notify("TaskerKeeper sidebar " + (state.enabled ? "on" : "off"));
      return;
    }
    notify("Usage: /tk-sidebar on|off|width N|status", "warning");
  }

  // Panel accessor for pi-sidebar-tui style compositors: returns cached lines.
  function getLines() {
    return state.lines.slice();
  }

  function getState() {
    return {
      enabled: state.enabled,
      width: state.width,
      height: state.height,
      todo: state.todo,
      error: state.error,
      lineCount: state.lines.length,
    };
  }

  // Prime the cache so the first paint has content even before events fire.
  refresh();

  // Event hooks: refresh on session start and after each turn/message.
  for (const event of ["session_start", "turn_end", "message_end"]) {
    try {
      pi.on(event, async () => {
        refresh();
      });
    } catch (_err) {
      // Older hosts may reject unknown events — remaining hooks still apply.
    }
  }

  // Slash command. Modern hosts take (name, def); accept legacy { name } too.
  try {
    pi.registerCommand("tk-sidebar", {
      description: "TaskerKeeper sidebar: on|off|width N|status",
      handler: handleTkSidebar,
    });
  } catch (_err) {
    try {
      pi.registerCommand({
        name: "tk-sidebar",
        description: "TaskerKeeper sidebar: on|off|width N|status",
        handler: handleTkSidebar,
      });
    } catch (_err2) {
      // Read-only panel still works via getLines() without the toggle.
    }
  }

  return { refresh, getLines, getState, handleTkSidebar };
}

module.exports = taskerkeeperSidebarExtension;
module.exports.default = taskerkeeperSidebarExtension;
module.exports.fetchPayload = fetchPayload;
module.exports.resolveTodo = resolveTodo;
