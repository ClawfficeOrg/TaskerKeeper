// Paseo server half: read-only RPC over the TaskerKeeper sidebar CLI.
//
// Exposes one RPC method:
//
//   taskerkeeper.sidebar { todo?, width?, height? } -> { ok, payload? | error? }
//
// The todo path resolves from (in order): RPC params, plugin settings,
// TASKERKEEPER_TODO env. The server never writes to the todo file — it
// spawns `taskerkeeper sidebar <todo> --json --width --height` and returns
// the parsed payload. Any failure is fail-soft: { ok: false, error }.
// No DAG logic lives here; taskerkeeper/cli.py owns all scheduling.
"use strict";

const { execFile } = require("node:child_process");

function resolveTodo(params, settings) {
  params = params || {};
  settings = settings || {};
  return (
    params.todo || settings.todo || process.env.TASKERKEEPER_TODO || ""
  );
}

function toInt(value, fallback) {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

// Paseo calls handle({ method, params }, ctx). ctx.settings holds the
// plugin settings when the host provides them; params win per call.
async function handle(request, ctx) {
  const method = request && request.method;
  if (method !== "taskerkeeper.sidebar") {
    return { ok: false, error: "unknown method: " + method };
  }
  const params = (request && request.params) || {};
  const settings = (ctx && ctx.settings) || {};
  const todo = resolveTodo(params, settings);
  const width = toInt(
    params.width !== undefined ? params.width : settings.width,
    80
  );
  const height = toInt(
    params.height !== undefined ? params.height : settings.height,
    40
  );
  if (!todo) {
    return {
      ok: false,
      error: "no todo file configured (settings.todo or TASKERKEEPER_TODO)",
    };
  }
  return new Promise((resolve) => {
    const args = ["sidebar", todo, "--json", "--width", String(width), "--height", String(height)];
    const opts = { timeout: 30000, maxBuffer: 4 * 1024 * 1024 };
    const runFallback = () =>
      execFile("python", ["-m", "taskerkeeper", ...args], opts, (err2, stdout2, stderr2) => {
        if (err2) {
          const detail = String(((stderr2 || stdout2 || err2.message || err2)).trim());
          resolve({ ok: false, error: detail.slice(-500) });
          return;
        }
        finish(stdout2);
      });
    const finish = (stdout) => {
      try {
        resolve({ ok: true, payload: JSON.parse(stdout) });
      } catch (parseErr) {
        resolve({
          ok: false,
          error: "invalid JSON from sidebar command: " + parseErr.message,
        });
      }
    };
    execFile("taskerkeeper", args, opts, (err, stdout, stderr) => {
      if (err) {
        // Stale/global installs may lack the sidebar subcommand; fall back
        // to the module invocation of the same CLI before giving up.
        const detail = String(((stderr || stdout || err.message || err)).trim());
        if (/invalid choice: 'sidebar'|not recognized|not found/i.test(detail)) {
          runFallback();
          return;
        }
        resolve({ ok: false, error: detail.slice(-500) });
        return;
      }
      finish(stdout);
    });
  });
}

module.exports = { handle, resolveTodo };
