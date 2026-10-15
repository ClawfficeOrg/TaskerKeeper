// Host-scoped plugin settings, edited under Settings → Plugins → paseo-tidetable.
// Every field falls back to an environment variable on the daemon (see
// server/sidebar.ts), so an empty document still works.
import { defineSettings } from "@getpaseo/plugin";
import { z } from "zod";

export const connection = defineSettings({
  id: "connection",
  scope: "host",
  version: 1,
  schema: z.object({
    // A todo file path, or the project slug when `server` is set.
    todo: z.string().default(""),
    // Base URL of a `tidetable serve` hub; empty reads the local file.
    server: z.string().default(""),
    // Stored as plain host-side JSON that every connected client can read.
    // TIDETABLE_TOKEN in the daemon's environment is the safer place.
    token: z.string().default(""),
    width: z.number().int().min(20).default(80),
    height: z.number().int().min(10).default(40),
  }),
});
