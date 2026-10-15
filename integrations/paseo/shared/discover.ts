// Find the TideTable todo files inside one workspace, so the right-sidebar
// panel follows whichever project is open instead of one configured path.
import { defineRpc } from "@getpaseo/plugin";
import { z } from "zod";

export const todoFile = z.object({
  path: z.string(),
  // Relative to the workspace directory, with forward slashes, for display.
  relative: z.string(),
  name: z.string(),
  modifiedMs: z.number(),
});

export type TodoFile = z.output<typeof todoFile>;

export const discoverRpc = defineRpc({
  name: "tidetable.discover",
  input: z.object({ directory: z.string().min(1) }),
  output: z.object({
    // Most recently modified first: the active roadmap is usually the one being edited.
    files: z.array(todoFile),
    // The scan hit its file budget; a deeper todo may be missing.
    truncated: z.boolean(),
  }),
});
