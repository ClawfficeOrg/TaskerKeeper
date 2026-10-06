/**
 * TaskerKeeper sidebar block for the OpenCode TUI.
 *
 * Public TUI plugin style (opencode-harness-panel pattern): a `tui` factory
 * that registers a `sidebar_content` slot rendering a collapsible
 * TaskerKeeper block. On each render it shells out to the shared core:
 *
 *   taskerkeeper sidebar <todo> --json --width <w> --height <h>
 *
 * and renders the five sections from that payload: Current, Concurrent,
 * Upcoming, phase tree, overall tree. Read-only: this file never writes to
 * the todo file. All DAG/scheduling logic lives in taskerkeeper/sidebar.py;
 * this adapter only renders. Touches arrive already basenamed from core, so
 * no absolute paths leak. Fail-soft: any CLI error renders a one-liner and
 * never throws.
 *
 * No runtime npm deps: only node builtins. The TUI plugin types are used
 * loosely (any) so the file loads without @opencode-ai/plugin installed.
 */

import { execFileSync } from "node:child_process";
import * as path from "node:path";

/** Loose stand-in for the host's TuiPlugin type: (api, options, meta) => Promise<void>. */
export type TuiPlugin = (api: any, options?: any, meta?: any) => Promise<void>;

/** Plugin options as passed via the tui.json tuple form. */
export type TkSidebarOptions = {
  /** Todo file path. Absolute, or relative to the session directory. */
  todoFile?: string;
  /** Sidebar width budget forwarded to `taskerkeeper sidebar --width`. */
  width?: number;
  /** Sidebar height budget forwarded to `taskerkeeper sidebar --height`. */
  height?: number;
  /** Slot order. Internal sidebar blocks use 100-500; default sits with them. */
  order?: number;
  /** Start collapsed (header + counts only). Default false. */
  collapsed?: boolean;
};

type TaskRow = {
  id?: string;
  title?: string;
  icon?: string;
  status?: string;
  owner?: string;
  age?: string;
  goal?: string;
  touches?: string[];
  blocked_by?: string[];
};

type SidebarPayload = {
  current?: { tasks?: TaskRow[]; more?: number };
  concurrent?: { tasks?: TaskRow[]; more?: number; deferred?: number };
  upcoming?: { tasks?: TaskRow[]; more?: number };
  phase?: { id?: string; title?: string; tree?: string[]; more?: number };
  overall?: { tree?: string[]; more?: number };
};

const DEFAULT_TODO = path.join("docs", "todo-sidebars.json");
const CLI_TIMEOUT_MS = 3000;

/** Resolve the todo file: explicit option, else $TASKERKEEPER_TODO, else default under the session directory. */
export function resolveTodoFile(api: any, options?: TkSidebarOptions): string {
  const explicit = options?.todoFile ?? process.env["TASKERKEEPER_TODO"];
  if (explicit) {
    if (path.isAbsolute(explicit)) return explicit;
    return path.resolve(baseDir(api), explicit);
  }
  return path.resolve(baseDir(api), DEFAULT_TODO);
}

function baseDir(api: any): string {
  const dir =
    api?.state?.path?.directory ??
    api?.state?.path?.worktree ??
    process.cwd();
  return String(dir);
}

/** Shell out to the shared core. Throws on CLI failure; callers turn that into the one-liner. */
export function loadPayload(todoFile: string, width: number, height: number): SidebarPayload {
  const args = ["sidebar", todoFile, "--json", "--width", String(width), "--height", String(height)];
  try {
    const out = execFileSync("taskerkeeper", args, {
      encoding: "utf8",
      timeout: CLI_TIMEOUT_MS,
      stdio: ["ignore", "pipe", "pipe"],
    });
    return JSON.parse(String(out)) as SidebarPayload;
  } catch (e: any) {
    // Fall back to the module: boxes whose console script is stale (no
    // `sidebar` command) or missing still have the package importable.
    const out = execFileSync("python", ["-m", "taskerkeeper", ...args], {
      encoding: "utf8",
      timeout: CLI_TIMEOUT_MS,
      stdio: ["ignore", "pipe", "pipe"],
    });
    return JSON.parse(String(out)) as SidebarPayload;
  }
}

function fmtTask(t: TaskRow): string[] {
  const head =
    `${t.icon ?? "·"} ${t.id ?? "?"} — ${t.title ?? ""}` +
    (t.owner ? ` [${t.owner}${t.age ? ` ${t.age}` : ""}]` : "") +
    (t.blocked_by?.length ? ` [waits: ${t.blocked_by.join(", ")}]` : "");
  const lines = [head];
  if (t.goal) lines.push(`  ${t.goal}`);
  if (t.touches?.length) lines.push(`  touches: ${t.touches.join(", ")}`);
  return lines;
}

function section(lines: string[], title: string, rows: string[], more: number): void {
  lines.push(title);
  lines.push("─".repeat(Math.min(50, Math.max(10, title.length + 8))));
  if (rows.length === 0) lines.push("  (none)");
  else for (const r of rows) lines.push(`  ${r}`);
  if (more > 0) lines.push(`  …${more} more`);
}

/** Render the five payload sections as plain text with more-counts. Never throws. */
export function renderText(payload: SidebarPayload, collapsed: boolean): string {
  const cur = payload.current ?? {};
  const con = payload.concurrent ?? {};
  const up = payload.upcoming ?? {};
  const ph = payload.phase ?? {};
  const ov = payload.overall ?? {};
  const curTasks = cur.tasks ?? [];
  const conTasks = con.tasks ?? [];
  const upTasks = up.tasks ?? [];
  const lines = ["TaskerKeeper ▼", "═".repeat(24)];
  if (collapsed) {
    lines.push(
      `current ${curTasks.length}${cur.more ? ` (+${cur.more})` : ""} · ` +
        `concurrent ${conTasks.length}${con.more ? ` (+${con.more})` : ""} · ` +
        `upcoming ${upTasks.length}${up.more ? ` (+${up.more})` : ""}`,
    );
    return lines.join("\n");
  }
  section(
    lines,
    "Current",
    curTasks.flatMap(fmtTask),
    cur.more ?? 0,
  );
  const conRows = conTasks.flatMap(fmtTask);
  section(lines, "Concurrent (safe to fan out)", conRows, con.more ?? 0);
  if ((con.deferred ?? 0) > 0) lines.push(`  (${con.deferred} deferred by touches overlap)`);
  section(
    lines,
    "Upcoming",
    upTasks.flatMap(fmtTask),
    up.more ?? 0,
  );
  section(lines, `Phase ${ph.id ?? "?"}: ${ph.title ?? ""}`, ph.tree ?? [], ph.more ?? 0);
  section(lines, "Overall", ov.tree ?? [], ov.more ?? 0);
  return lines.join("\n");
}

/** One render pass: spawn the CLI, format, or fail soft. Never throws. */
export function renderOnce(api: any, options?: TkSidebarOptions): string {
  try {
    const width = Math.max(20, options?.width ?? 60);
    const height = Math.max(10, options?.height ?? 40);
    const todoFile = resolveTodoFile(api, options);
    const payload = loadPayload(todoFile, width, height);
    return renderText(payload, options?.collapsed ?? false);
  } catch {
    return "TaskerKeeper unavailable";
  }
}

/**
 * TUI factory (named export). Registers the `sidebar_content` slot; the slot
 * callback re-spawns the CLI on every render so the block stays fresh.
 */
export const tui: TuiPlugin = async (api: any, options?: TkSidebarOptions): Promise<void> => {
  const order = options?.order ?? 400;
  api.slots.register({
    order,
    slots: {
      // (ctx, props) signature matches the host; props carry { session_id }.
      sidebar_content: () => renderOnce(api, (options ?? {}) as TkSidebarOptions),
    },
  });
};

/** Default export for loader compat: file modules resolve `default { id, tui }`. */
const plugin = {
  id: "taskerkeeper-sidebar",
  tui,
};

export default plugin;
