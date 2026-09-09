# TaskerKeeper Philosophy

## The Problem with Markdown Todos

Markdown todo files (`docs/todo-v*.md`) are the de facto standard for planning
autonomous agent work. They work well for humans — linear, readable, git-friendly.
But they break down when agents are the primary consumers:

### Positional IDs Are Fragile
Task IDs are line numbers. Inserting task `v9.0` between `v8.x` and `v10.0`
means renumbering everything below. This creates merge conflicts, confuses
agents, and wastes tokens on re-verification.

### No Dependency Expression
Prerequisites are prose in the "Goal" field. Agents have to parse natural
language to determine what's blocked. This is fragile and error-prone.

### No Parallel Execution
Everything is linear by design. If tasks A and B are independent and both
ready, the agent still processes them one after the other, because nothing in
the format says they are independent — and nothing says which files each of
them owns, so nothing could tell you whether running them together is safe.

### Token Waste
Agents read the entire file even when they only need one task. For large
roadmaps (50+ tasks), this is significant overhead.

## The TaskerKeeper Solution

TaskerKeeper replaces markdown todos with structured JSON that has:

1. **Stable IDs** — Task `7.0.3` is always `7.0.3`, regardless of where it
   sits in the file. Insert, delete, or reorder tasks without breaking
   anything.

2. **Dependency DAG** — Each task declares its prerequisites by ID. A task
   is eligible only when all its prerequisites are `done`. This enables
   non-linear execution paths.

3. **Safe Fan-Out** — `taskerkeeper ready` emits the whole runnable set at
   once, and `--disjoint` narrows it to tasks whose `touches` paths do not
   collide, so a supervisor can dispatch the set without two agents editing one
   file. `parallel_group` is a label on top of that, for reading — it groups
   related work in `parallel` and `convert` output and has no effect on
   scheduling. Real ordering is a `prerequisite`; there is deliberately only one
   mechanism that can block a task.

   Claiming completes the picture: `start` records an owner and a lease, so a
   second agent is never handed work someone is already doing, and work orphaned
   by a crash returns to the pool on its own.

4. **Machine-Readable Structure** — No regex parsing, no line-number counting.
   JSON is native to every language and tool.

5. **Version Mapping** — Planning milestones (v7) map to release versions
   (0.7.0) at the phase level. Task IDs stay stable across releases.

## Design Principles

### Stable IDs Above All
The single most important property is that task IDs never change. Everything
else — file format, tooling, workflow — serves this goal. Stable IDs mean:

- Git diffs are clean (no renumbering noise)
- Agents can reference tasks reliably across sessions
- Migration between tools is trivial
- History is traceable

### Pragmatic, Not Perfect
TaskerKeeper doesn't try to replace Jira, Linear, or GitHub Issues. It's
specifically designed for autonomous agent workflows where:

- The "user" is an AI agent, not a human clicking buttons
- Speed and token efficiency matter more than UI polish
- The file is read and written programmatically, not edited by hand
- Integration with ralph-style autonomous loops is the primary use case

### Backward Compatible
TaskerKeeper coexists with markdown todos. You can:

- Render JSON → markdown for human review (`taskerkeeper convert`)
- Run both formats side by side during migration
- Use the same task ID scheme in both formats

Markdown → JSON conversion is deliberately not automated: the interesting part
of a migration is deciding what the prose prerequisites actually meant, and a
parser would guess. Model the roadmap in JSON by hand, then `validate`.

### Minimal Dependencies
- **JSON** — universal, no tools required
- **Python 3.10+** — for the CLI, with `jsonschema` as the single runtime
  dependency (schema validation is the one thing not worth reimplementing)
- **No jq** — `ralph/ralph-json.sh` forwards to the CLI rather than
  reimplementing the DAG logic in bash
- No databases, no servers, no complex setup

## When to Use What

| Scenario | Format |
|----------|--------|
| <20 tasks, linear execution | Markdown is fine |
| >20 tasks or parallel work | TaskerKeeper JSON |
| Frequent task insertion/deletion | TaskerKeeper JSON |
| Agent-driven development | TaskerKeeper JSON |
| Human-only review | Markdown (or convert from JSON) |
| CI/CD integration | TaskerKeeper JSON |

## Future Directions

- **GitHub integration** — Sync task status with GitHub Issues
- **Multi-file support** — Split large roadmaps across files
- **Diff format** — A compact format for representing task changes
