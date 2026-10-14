/**
 * TideTable adjudicator bridge for OpenCode.
 *
 * The TKRun supervisor decides whether an unattended agent may run a shell
 * command. Most commands it settles with its own allow/deny lists; a command on
 * neither list is put to a decision model (clef). Because OpenCode owns the
 * shell, the supervisor exposes that decision as a local service and this plugin
 * asks it: on the `execute.before` hook for a bash/shell command it reads the
 * service address (from the environment, or a discovery file in the session
 * directory), sends the command to the supervisor, and throws when the answer is
 * not `allow` — which aborts the tool.
 *
 * Contract (all the logic lives supervisor-side; this file only asks):
 *   one line of JSON over loopback TCP: {"command": "..."} + "\n"
 *   -> {"decision": "allow"|"deny", "confidence": number, "reason": string}
 *
 * Fail-closed: no service address, a socket error, or any non-`allow` verdict
 * throws. That is deliberate — an unattended agent must not run a command the
 * adjudicator did not clear. When there is no service address (an attended
 * session, or the adjudicator disabled) the plugin does nothing.
 *
 * Dependency-free: node builtins only, mirroring `tk-sidebar`. The plugin types
 * are loose (`any`) so the file loads without `@opencode-ai/plugin`.
 */

import { readFileSync } from "node:fs";
import { connect } from "node:net";
import * as path from "node:path";

type Discovery = { host: string; port: number; home?: string };

interface ToolBeforeEvent {
  readonly tool: string;
  input: unknown;
}

interface PluginContext {
  readonly directory?: string;
  readonly worktree?: string;
  readonly tool: {
    hook(
      name: "execute.before",
      handler: (event: ToolBeforeEvent) => void | Promise<void>,
    ): Promise<unknown>;
  };
}

const DISCOVERY = path.join(".overnight", "adjudicator.json");

function readDiscovery(start: string): Discovery | undefined {
  // The runner passes the service address in the environment (works in every job worktree);
  // the per-worktree discovery file is the fallback for a manually launched agent.
  const envHost = process.env.TIDETABLE_ADJUDICATOR_HOST;
  const envPort = Number(process.env.TIDETABLE_ADJUDICATOR_PORT || "");
  if (envHost && Number.isInteger(envPort) && envPort > 0) return { host: envHost, port: envPort };
  let dir = start;
  for (;;) {
    try {
      const raw = readFileSync(path.join(dir, DISCOVERY), "utf8");
      const parsed = JSON.parse(raw) as Discovery;
      if (parsed && typeof parsed.port === "number" && typeof parsed.host === "string") return parsed;
    } catch {
      // no file here; keep walking up
    }
    const parent = path.dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

function ask(
  discovery: Discovery,
  command: string,
): Promise<{ decision: string; reason?: string; confidence?: number }> {
  return new Promise((resolve) => {
    const sock = connect({ host: discovery.host, port: discovery.port });
    let data = "";
    let settled = false;
    const done = (verdict: { decision: string; reason?: string; confidence?: number }): void => {
      if (settled) return;
      settled = true;
      sock.destroy();
      resolve(verdict);
    };
    sock.on("connect", () => sock.write(JSON.stringify({ command }) + "\n"));
    sock.on("data", (chunk) => {
      data += chunk.toString("utf8");
      if (data.includes("\n")) {
        try {
          return done(JSON.parse(data.trim()) as { decision: string });
        } catch {
          return done({ decision: "deny", reason: "unparseable verdict" });
        }
      }
    });
    sock.on("error", (err) => done({ decision: "deny", reason: `socket error: ${err.message}` }));
    sock.on("close", () => {
      if (!data) done({ decision: "deny", reason: "no verdict" });
    });
    setTimeout(() => done({ decision: "deny", reason: "adjudicator timeout" }), 20_000);
  });
}

export default {
  id: "tidetable-adjudicator",
  async setup(ctx: PluginContext) {
    const start = ctx.worktree || ctx.directory || process.cwd();
    const discovery = readDiscovery(start);
    if (!discovery) {
      // Not an unattended run (or the adjudicator is off): nothing to enforce.
      return;
    }
    await ctx.tool.hook("execute.before", async (event: ToolBeforeEvent) => {
      const tool = event.tool.toLowerCase();
      if (tool !== "bash" && tool !== "shell") return;
      const args = event.input as Record<string, unknown> | undefined;
      const command = args?.command;
      if (typeof command !== "string" || !command.trim()) return;
      const verdict = await ask(discovery, command);
      if (verdict.decision !== "allow") {
        const why = verdict.reason ? ` (${verdict.reason})` : "";
        throw new Error(`TideTable adjudicator denied this command${why}: ${command}`);
      }
    });
  },
};
