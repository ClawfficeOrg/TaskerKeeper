# Overnight runner

**TKRun.** `tidetable tkrun run <todo.json>` (`overnight run` is a kept alias)
works through a todo file unattended, one task at a time, inside a **git
worktree** on a throwaway branch `overnight/<date>` cut from the base branch.
Your own checkout is never edited (dirty, on another branch, open in an editor:
all fine), and the branch is never merged: you review and merge it in the
morning. It commits, but never pushes, tags, or touches the base branch.

## Any repo, three steps

```bash
tidetable tkrun init docs/todo.json            # writes .tidetable/overnight.json
tidetable tkrun run  docs/todo.json --dry-run  # routing, gate, command, prompt; changes nothing
tidetable tkrun run  docs/todo.json --hours 8  # go (commit todo.json changes first)
```

`init` infers a gate from `Cargo.toml`, `package.json`, `pyproject.toml` or
`go.mod`. With no config file at all the same inference applies. Stop early
with `tidetable tkrun stop docs/todo.json` (after the current task), or
`tidetable tkrun kill docs/todo.json` to hard-stop now (sentinel plus a
process-tree signal; see below). Keep the PC awake; a sleeping machine ends the
run.

## Per task

1. Claim the first ready pending task (`start --owner overnight-<date> --lease`).
2. Run the agent its tier resolves to (`tidetable agents show`):
   `anthropic` → `claude -p`, `opencode-go` → `opencode run`, anything else via
   `agent_commands`. No skip-permissions flag: the shell allow/deny lists below
   are passed to claude, and written to a temporary `opencode.json` for opencode.
3. The **runner** runs your gate commands. It does not trust the agent's report.
4. A task reported DONE gets one read-only **task review** of its diff against
   the success criteria (optional, on by default). Only PASS closes it.
   When the last task of a phase closes, an optional **phase review** (off by
   default) looks at the phase as a whole against the phase goal. It uses its
   own model, so a cheap model can review each task and a stronger one each
   phase, or the reverse. A phase FAIL leaves the tasks closed, logs
   `PHASE REVIEW FAIL <phase>: <reasons>`, and with `on_fail: "stop"` ends the
   run there.
5. Outcome:

   | Outcome | When | Result |
   |---|---|---|
   | done | DONE, gate passed, required files changed, review PASS | `done --changelog`, commit `<id>: <summary>` |
   | partial | PARTIAL, review FAIL/not run, required file missing | commit `wip(<id>)`, task stays `in_progress` for you |
   | failed | FAILED, no result line, timeout, gate failed, DONE with no changes | work stashed (`overnight <date> <id> failed`), task back to pending |
   | skipped | needs a human and changed nothing; provider out of quota | task back to pending or never claimed |

## Running tasks in parallel

`--jobs N` (or `parallel.jobs` in the config) runs up to N tasks at once. Each
gets its own worktree and branch (`overnight/<date>-<id>`) cut from the run
branch, and is merged back with `--no-ff` as it finishes, one at a time. The
todo file is only ever written in the run worktree, so jobs never conflict on
task state.

- A task runs next to others only if it declares `touches` and none of its
  paths overlaps (same path, or one inside the other) with a running task's. A
  task with no `touches` is exclusive: it waits for the running ones to drain,
  and nothing starts behind it.
- A merge conflict leaves the job branch and worktree in place, puts the task
  back to pending, and counts as a failure.
- Cargo (and other tools that lock a shared directory) serialise on it. Set
  `parallel.job_env`, e.g. `{"CARGO_TARGET_DIR": "{home}/.overnight/target"}`, to
  share one build directory instead of compiling every worktree from scratch.
  `{home}` expands to the checkout.
- `--follow` output is prefixed with the task id.
- Not supported together with `siblings` yet.

The deadline only stops new tasks from *starting*: a task already running is
never cut short by it, only by `limits.task_minutes` (default 120).

Each task is tried once per run; two failures in a row end the run. Usage
limits (429, "resets 3am", `limit reached|<epoch>`) are slept through, then the
agent is told to continue its partial work; a reset past the deadline ends that
provider's tasks for the night.

Human-check rule: a criterion counts only if a test proves it or it is a
mechanical fact. Criteria matching `human_check_pattern` (visual words by
default) are flagged in the prompt; expect those tasks to end PARTIAL.

## `.tidetable/overnight.json`

All keys optional; unknown keys are an error.

| Key | Meaning |
|---|---|
| `base_branch`, `branch_prefix` | `main`, `overnight` (`--base` overrides the base per run) |
| `worktree_dir` | where the worktree goes (default `<repo>-overnight-<date>`, beside the repo) |
| `project` | one line naming the repo, used in the prompt |
| `prompt_prefix` | text prepended to every prompt (e.g. a terse-output mode) |
| `rules` | extra hard rules appended to the prompt |
| `extra_paths` | paths besides the task's `touches` the agent may edit |
| `shell_allow`, `shell_deny` | added to the built-in read-only-git allow and no-commit/no-state deny lists |
| `deny_edit` | globs the agent must never edit (generated files) |
| `external_read` | directories outside the repo the agent may read (`~` and env vars expand) |
| `gate` | steps, below |
| `required_changed` | a DONE that did not change these (e.g. `CHANGELOG.md`) becomes PARTIAL |
| `siblings` | `[{path, scope[]}]` other repos a task may change. Each gets its own worktree `<name>-overnight-<date>` on the same branch name, cut from the branch it is on. In config strings `{sibling:PATH}` is that worktree and `{sibling_home:PATH}` the real checkout (for prebuilt tools) |
| `review` | `{enabled, model, phase: {enabled, model, on_fail}}`. `enabled`/`model` are the per-task review (default Opus, `provider/model`). `phase` is the optional end-of-phase review: off by default, `model` defaults to the task reviewer, `on_fail` is `continue` (default) or `stop`. Flags: `--task-review-model`, `--no-task-review`, `--phase-review-model` (also enables it), `--no-phase-review`; `--review-model` and `--no-review` are the older names for the task review |
| `parallel` | `{jobs, job_env}`: concurrent tasks (default 1) and extra env for every agent and gate process, see "Running tasks in parallel" |
| `limits` | `task_minutes` 120, `gate_minutes` 45, `max_consecutive_failures` 2, `lease_minutes` 480 |
| `agent_commands` | `{provider: {cmd: [... "{model}" "{prompt}"], stdin: bool}}` for other CLIs |
| `human_check_pattern`, `human_check_exempt` | regexes for criteria that need a person |

Gate step: `{name, cmd[], cwd, repo, when_changed[], append_changed, exclude[], fail_output_regex}`.
`repo` is `"."` or a sibling `path`; `when_changed` skips the step unless one
of that repo's changed files matches; `when_repo_changed` runs it only if that
repo (`"."` or a sibling path) has any change; `append_changed` appends the matching
changed files to `cmd` (e.g. `rustfmt --check` on touched `.rs`);
`fail_output_regex` fails a zero-exit step whose output matches.

Examples: this repo's `.tidetable/overnight.json` equivalent for a Rust app
with a vendored sibling lives in Zoid's `examples/specttyr`.

## Files and safety

- Log: `.overnight/log.md`; transcripts and gate output: `.overnight/<date>/`;
  STOP and KILL sentinels and the pid file in `.overnight/`. The runner adds
  `.overnight/` and `opencode.json` to `.git/info/exclude`.
- Refuses to start with uncommitted changes to the todo file (the worktree has
  the committed copy), a worktree path already used by another branch, or while
  another run is alive. Each agent call re-checks that the worktree is still on
  the run branch and that the base branch did not move. A second run the same
  day resumes the existing worktree and branch.
- Build output is per worktree: the first gate in a fresh worktree compiles from
  scratch. Repos needing submodules or untracked setup must prepare that
  themselves.
- `tkrun stop` is cooperative: it writes the `STOP` sentinel, noticed between
  tasks, so the current one finishes. `tkrun kill` is the hard stop: it writes
  `KILL` *and* tree-kills the runner PID, so a mid-task agent subprocess dies
  too. Task claims are left to expire on their own.
- After a hard kill the task stays `in_progress` with uncommitted changes in the
  worktree; `tidetable reset` it there and inspect `git status`.

## Morning

Read `.overnight/log.md` (in your own checkout); `git log --stat <base>..overnight/<date>`; for each
`wip(<id>)` check the listed criteria then `tidetable done`; look at
`git stash list` for failed attempts; then merge or cherry-pick, and clean up
with `git worktree remove <path>` and `git branch -d overnight/<date>`.

## Known risks

- Agents are not sandboxed beyond the tool allowlists. Shell allowlists are
  prefix rules and build/test commands run code the agent wrote: treat the
  branch as untrusted until reviewed.
- opencode's `--auto` approves everything not explicitly denied (edits
  anywhere in the repo, web fetches). A leftover `opencode.json` after a hard
  kill also restricts your interactive opencode there; delete it.
- On Windows an npm-installed `opencode` is a `.cmd` shim, where a multi-line
  prompt argument may be mangled; use a real binary or an `agent_commands`
  entry that reads stdin.
- The Claude CLI flags were verified only against a stub. Run `--dry-run`,
  then a short real run (`--hours 0 --minutes 20`) before trusting a long one.

## Leased lanes (`lanes`)

A provider that only answers inside a reserved window (for example SingularityAPI lanes)
goes under `lanes` in `.tidetable/overnight.json`, and a tier points at it with
`tidetable agents set <tier> --provider singularity --model <id>`:

```json
"lanes": {"singularity": {"margin_minutes": 20, "fallback": "opencode-go/muse-spark-1.3-contributor"}}
```

The runner only reads `GET /reservations?status=all`; it never books, and it merges back-to-back
hours into one window. Before each task it uses the lane when more than `margin_minutes` remain,
otherwise it routes that task to `fallback` (a missing key, a network error or no reservation also
mean fallback). A task already running when the lease ends is not interrupted. Keys come only from
the environment, so run under `infisical run`: `reservation_key_env`, `endpoint_env`, `key_env`
(defaults are the `SINGULARITY_*` names). The generated `opencode.json` holds `{env:...}`
references, never values. `model_options` default to `{"reasoning_effort": "none"}`.
One task runs at a time, so a lane's second request slot is unused.
