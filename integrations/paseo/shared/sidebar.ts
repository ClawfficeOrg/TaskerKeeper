// The one RPC between the panel and the daemon: the parsed output of
// `tidetable sidebar <todo> --json`. tidetable/sidebar.py owns the shape; the
// schemas here are loose on purpose so a newer CLI that adds a field does not
// make every panel fail output validation.
import { defineRpc } from "@getpaseo/plugin";
import { z } from "zod";

const task = z.looseObject({
  id: z.string(),
  title: z.string(),
  icon: z.string().optional(),
  status: z.string().optional(),
  owner: z.string().optional(),
  age: z.string().optional(),
  goal: z.string().optional(),
  touches: z.array(z.string()).optional(),
  blocked_by: z.array(z.string()).optional(),
});

const taskList = z.looseObject({
  tasks: z.array(task).default([]),
  more: z.number().default(0),
});

const run = z.looseObject({
  status: z.string(),
  date: z.string().nullish(),
  branch: z.string().nullish(),
  deadline: z.string().nullish(),
  last: z.string().nullish(),
});

export const sidebarPayload = z.looseObject({
  current: taskList,
  concurrent: taskList,
  upcoming: taskList,
  phase: z.looseObject({
    id: z.string().default("?"),
    title: z.string().default(""),
    tree: z.array(z.string()).default([]),
    more: z.number().default(0),
  }),
  overall: z.looseObject({
    tree: z.array(z.string()).default([]),
    more: z.number().default(0),
  }),
  run: run.nullish(),
});

export type SidebarPayload = z.output<typeof sidebarPayload>;
export type SidebarTask = z.output<typeof task>;

// Fail-soft: a missing todo or a CLI error is a result the panel renders,
// not a rejected RPC that lands in the host's error boundary.
export const sidebarRpc = defineRpc({
  name: "tidetable.sidebar",
  input: z.object({}),
  output: z.discriminatedUnion("ok", [
    z.object({ ok: z.literal(true), payload: sidebarPayload }),
    z.object({ ok: z.literal(false), error: z.string() }),
  ]),
});
