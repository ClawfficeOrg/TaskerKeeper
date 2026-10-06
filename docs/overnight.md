# Overnight runner

`taskerkeeper overnight run <todo.json>` works through a todo file unattended,
one task at a time, inside a **git worktree** on a throwaway branch
`overnight/<date>` cut from the base branch. Your own checkout is never edited
(dirty, on another branch, open in an editor: all fine), and the branch is
never merged: you review and merge it in the morning. It commits, but never
pushes, tags, or touches the base branch.

## Any repo, three steps

```bash
taskerkeeper overnight init docs/todo.json            # writes .taskerkeeper/overnight.json
taskerkeeper overnight run  docs/todo.json --dry-run  # routing, gate, command, prompt; changes nothing
taskerkeeper overnight run  docs/todo.json --hours 8  # go (commit todo.json changes first)
```

`init` infers a gate from `Cargo.toml`, `package.json`, `pyproject.toml` or
`go.mod`. With no config file at all the same inference applies. Stop early
with `taskerkeeper overnight stop docs/todo.json` (after the current task).
Keep the PC awake; a sleeping machine ends the run.

## Per task

1. Claim the first ready pending task (`start --owner overnight-<date> --lease`).
2. Run the agent its tier resolves to (`taskerkeeper agents show`):
   `anthropic` → `claude -p`, `opencode-go` → `opencode run`, anything else via
   `agent_commands`. No skip-permissions flag: the shell allow/deny lists below
   are passed to claude, and written to a temporary `opencode.json` for opencode.
3. The **runner** runs your gate commands. It does not trust the agent's report.
4. A task reported DONE gets one read-only review of its diff against the
   success criteria. Only PASS closes it.
5. Outcome:

   | Outcome | When | Result |
   |---|---|---|
   | done | DONE, gate passed, required files changed, review PASS | `done --changelog`, commit `<id>: <summary>` |
   | partial | PARTIAL, review FAIL/not run, required file missing | commit `wip(<id>)`, task stays `in_progress` for you |
   | failed | FAILED, no result line, timeout, gate failed, DONE with no changes | work stashed (`overnight <date> <id> failed`), task back to pending |
   | skipped | needs a human and changed nothing; provider out of quota | task back to pending or never claimed |

The deadline only stops new tasks from *starting*: a task already running is
never cut short by it, only by `limits.task_minutes` (default 120).

Each task is tried once per run; two failures in a row end the run. Usage
limits (429, "resets 3am", `limit reached|<epoch>`) are slept through, then the
agent is told to continue its partial work; a reset past the deadline ends that
provider's tasks for the night.

Human-check rule: a criterion counts only if a test proves it or it is a
mechanical fact. Criteria matching `human_check_pattern` (visual words by
default) are flagged in the prompt; expect those tasks to end PARTIAL.

## `.taskerkeeper/overnight.json`

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
| `review` | `{enabled, model}` (default Opus, `provider/model`) |
| `limits` | `task_minutes` 120, `gate_minutes` 45, `max_consecutive_failures` 2, `lease_minutes` 480 |
| `agent_commands` | `{provider: {cmd: [... "{model}" "{prompt}"], stdin: bool}}` for other CLIs |
| `human_check_pattern`, `human_check_exempt` | regexes for criteria that need a person |

Gate step: `{name, cmd[], cwd, repo, when_changed[], append_changed, exclude[], fail_output_regex}`.
`repo` is `"."` or a sibling `path`; `when_changed` skips the step unless one
of that repo's changed files matches; `when_repo_changed` runs it only if that
repo (`"."` or a sibling path) has any change; `append_changed` appends the matching
changed files to `cmd` (e.g. `rustfmt --check` on touched `.rs`);
`fail_output_regex` fails a zero-exit step whose output matches.

Examples: this repo's `.taskerkeeper/overnight.json` equivalent for a Rust app
with a vendored sibling lives in Zoid's `examples/specttyr`.

## Files and safety

- Log: `.overnight/log.md`; transcripts and gate output: `.overnight/<date>/`;
  STOP sentinel and pid file in `.overnight/`. The runner adds `.overnight/`
  and `opencode.json` to `.git/info/exclude`.
- Refuses to start with uncommitted changes to the todo file (the worktree has
  the committed copy), a worktree path already used by another branch, or while
  another run is alive. Each agent call re-checks that the worktree is still on
  the run branch and that the base branch did not move. A second run the same
  day resumes the existing worktree and branch.
- Build output is per worktree: the first gate in a fresh worktree compiles from
  scratch. Repos needing submodules or untracked setup must prepare that
  themselves.
- Hard kill: the task stays `in_progress` with uncommitted changes in the
  worktree; `taskerkeeper reset` it there and inspect `git status`.

## Morning

Read `.overnight/log.md` (in your own checkout); `git log --stat <base>..overnight/<date>`; for each
`wip(<id>)` check the listed criteria then `taskerkeeper done`; look at
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
