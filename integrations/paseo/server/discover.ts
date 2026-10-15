// Read-only scan of a workspace directory for TideTable todo files. A todo is
// any JSON object carrying the schema's three required keys (schema_version,
// project, phases); nothing else is parsed or interpreted here.
import { readdir, readFile, stat } from "node:fs/promises";
import { basename, join, relative, sep } from "node:path";
import type { RpcOutput } from "@getpaseo/plugin";
import type { discoverRpc, TodoFile } from "../shared/discover";

const MAX_DEPTH = 4;
const MAX_ENTRIES = 5_000;
const MAX_BYTES = 5 * 1024 * 1024;
// Build output and dependency trees never hold a roadmap, and walking them is
// the whole cost of a scan. Dot-directories (.git, .tkrun, .tidetable config)
// are skipped too: tkrun.json and agents.json are config, not todos.
const SKIP = new Set(["node_modules", "target", "dist", "build", "out", "vendor", "venv", "__pycache__", "coverage"]);

export function isTodoDocument(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const doc = value as Record<string, unknown>;
  return (
    typeof doc.schema_version === "string" &&
    !!doc.project && typeof doc.project === "object" && !Array.isArray(doc.project) &&
    Array.isArray(doc.phases)
  );
}

async function readTodo(path: string, root: string): Promise<TodoFile | null> {
  try {
    const info = await stat(path);
    if (!info.isFile() || info.size > MAX_BYTES) return null;
    const text = await readFile(path, "utf8");
    // Cheap reject before JSON.parse on every package.json and tsconfig.
    if (!text.includes('"phases"')) return null;
    const doc = JSON.parse(text) as { project?: { name?: unknown } };
    if (!isTodoDocument(doc)) return null;
    const name = typeof doc.project?.name === "string" && doc.project.name ? doc.project.name : basename(path);
    return { path, relative: relative(root, path).split(sep).join("/"), name, modifiedMs: info.mtimeMs };
  } catch {
    return null;
  }
}

export async function discoverTodos(directory: string): Promise<RpcOutput<typeof discoverRpc>> {
  const files: TodoFile[] = [];
  let seen = 0;
  let truncated = false;
  const walk = async (dir: string, depth: number): Promise<void> => {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (++seen > MAX_ENTRIES) {
        truncated = true;
        return;
      }
      if (entry.name.startsWith(".")) continue;
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (depth < MAX_DEPTH && !SKIP.has(entry.name)) await walk(path, depth + 1);
      } else if (entry.isFile() && entry.name.endsWith(".json")) {
        const todo = await readTodo(path, directory);
        if (todo) files.push(todo);
      }
    }
  };
  await walk(directory, 0);
  files.sort((a, b) => b.modifiedMs - a.modifiedMs || a.relative.localeCompare(b.relative));
  return { files, truncated };
}
