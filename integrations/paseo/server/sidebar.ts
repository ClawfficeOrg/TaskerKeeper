// Read-only bridge to `tidetable sidebar --json`. No DAG logic lives here:
// tidetable/cli.py owns all scheduling, and this file only spawns it.
//
// Each setting falls back to its env var on the daemon (TIDETABLE_TODO,
// TIDETABLE_SERVER, TIDETABLE_TOKEN). The token reaches the child through its
// environment, never argv, so it does not show up in `ps`.
import { execFile, type ExecFileOptions } from "node:child_process";
import { statSync } from "node:fs";
import type { RpcOutput } from "@getpaseo/plugin";
import { sidebarPayload, type sidebarRpc } from "../shared/sidebar";

export interface SidebarConfig {
  todo: string;
  server: string;
  token: string;
  width: number;
  height: number;
}

type Result = RpcOutput<typeof sidebarRpc>;
type Env = Record<string, string | undefined>;

// A file, not merely a path that exists: a slug like "tidetable" names the
// package directory when the daemon's cwd is a checkout.
function isTodoFile(path: string): boolean {
  return statSync(path, { throwIfNoEntry: false })?.isFile() ?? false;
}

export function resolveConfig(
  values: Partial<SidebarConfig>,
  env: Env = process.env,
  isFile: (path: string) => boolean = isTodoFile,
): SidebarConfig {
  const pick = (value: string | undefined, name: string) =>
    String(value || env[name] || "").trim();
  const todo = pick(values.todo, "TIDETABLE_TODO");
  // A hub set in the plugin settings always wins. One inherited from the
  // daemon's environment does not turn an existing local file into a slug:
  // that sent "/path/todo.json" to the hub as "unknown slug".
  const explicit = String(values.server || "").trim();
  const inherited = String(env.TIDETABLE_SERVER || "").trim();
  return {
    todo,
    server: explicit || (inherited && !(todo && isFile(todo)) ? inherited : ""),
    token: pick(values.token, "TIDETABLE_TOKEN"),
    width: values.width ?? 80,
    height: values.height ?? 40,
  };
}

// The argv after the executable. Without a hub, --local keeps the CLI itself
// from honouring an inherited TIDETABLE_SERVER that resolveConfig declined.
export function sidebarArgs(config: SidebarConfig): string[] {
  const view = ["sidebar", config.todo, "--json", "--width", String(config.width), "--height", String(config.height)];
  return config.server ? view : ["--local", ...view];
}

// Stale or global installs may lack `sidebar` (or `tidetable` is not on the
// daemon's PATH at all); the module form of the same CLI is the fallback.
// Match only "could not run that command", never the CLI's own errors: its
// "Error: todo.json not found" once matched a bare /not found/ and was retried.
const COMMANDS: readonly (readonly string[])[] = [
  ["tidetable"],
  ...(process.platform === "win32" ? [["python", "-m", "tidetable"]] : [["python3", "-m", "tidetable"], ["python", "-m", "tidetable"]]),
];
const RETRYABLE = /ENOENT|invalid choice: 'sidebar'|is not recognized as an internal or external command|command not found|No module named tidetable/i;

function run(cmd: readonly string[], args: string[], options: ExecFileOptions) {
  return new Promise<{ ok: true; stdout: string } | { ok: false; detail: string }>((resolve) => {
    execFile(cmd[0], [...cmd.slice(1), ...args], { ...options, encoding: "utf8" }, (err, stdout, stderr) => {
      if (err) {
        resolve({ ok: false, detail: String(stderr || stdout || err.message).trim() });
      } else {
        resolve({ ok: true, stdout: String(stdout) });
      }
    });
  });
}

export async function readSidebar(config: SidebarConfig): Promise<Result> {
  if (!config.todo) {
    return {
      ok: false,
      error: config.server
        ? "no project configured (set Todo to the hub project slug)"
        : "no todo file configured (set Todo in the plugin settings, or TIDETABLE_TODO)",
    };
  }
  const options: ExecFileOptions = { timeout: 30_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true };
  if (config.server) {
    // Always set both: an empty token surfaces as the CLI's own 401 hint.
    options.env = { ...process.env, TIDETABLE_SERVER: config.server, TIDETABLE_TOKEN: config.token };
  }
  const args = sidebarArgs(config);
  let detail = "";
  for (const cmd of COMMANDS) {
    const res = await run(cmd, args, options);
    if (!res.ok) {
      detail = res.detail;
      if (RETRYABLE.test(detail)) continue;
      return { ok: false, error: detail.slice(-500) };
    }
    let json: unknown;
    try {
      json = JSON.parse(res.stdout);
    } catch {
      // Older `python -m tidetable` printed "Error: ..." and still exited 0.
      return { ok: false, error: res.stdout.trim().slice(-500) || "empty output from sidebar command" };
    }
    const parsed = sidebarPayload.safeParse(json);
    return parsed.success
      ? { ok: true, payload: parsed.data }
      : { ok: false, error: "unexpected sidebar payload: " + parsed.error.message.slice(0, 300) };
  }
  return { ok: false, error: ("tidetable CLI not found on the daemon's PATH. " + detail).slice(-500) };
}
