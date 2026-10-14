# Attended and unattended runs

TideTable has two run modes, and they differ only in **who approves a shell
command**. The mode is a permission model, not a second CLI: the commands,
the todo format, and the DAG logic are the same.

## Attended

You drive the session. Your own opencode or claude permissions apply, a person
sees each command before it runs, and the base branch is yours to touch. This is
the ordinary `tidetable start|done|reset|...` use, and the `serve` hub in front
of it.

The tool never calls a provider and never touches git on its own, so there is no
permission layer to speak of: whatever your harness allows is what happens.

## Unattended

**TKRun** (`tidetable tkrun run <todo.json>`, `overnight` kept as an alias) works
through a todo file on a throwaway worktree branch with no one watching. Two
things change because of that:

- **The runner owns history and state.** It commits, and it is the only writer of
  TideTable state. It never merges, pushes, or touches the base branch; the agent
  it spawns is not allowed to either.
- **The permission model is stricter.** `tkrun run` builds the agent's
  environment and permission lists itself. There is no flag that turns an
  attended session unattended; `tkrun` is the unattended entry point.

### Static lists (what runs today)

Every command is decided by lists in `tidetable/overnight.py`, passed to
`claude -p` or written into a temporary `opencode.json` for the call:

| List | Meaning |
|---|---|
| `BASE_SHELL_ALLOW` | Read-only inspection, safe in any repo: `git status\|diff\|log\|show\|ls-files`, `ls`, `pwd`, `wc`. A config's `shell_allow` adds to it. |
| `BASE_SHELL_DENY` | What must never happen unattended: `git push`/`tag` (`GIT_REMOTE_DENY`), local history rewrites (`GIT_LOCAL_DENY`), TideTable state writes (`TIDETABLE_DENY`), env/secret dumps (`SECRET_DENY`). |
| `OPENCODE_SHELL_DENY` | The smaller list opencode 2.x can live with. opencode drops its shell tool for the rest of the session after one rejected command, so only the must-never commands are denied there; local git is allowed and the runner folds an agent's own commits back into the working tree. |

`SECRET_DENY` blocks `env`, `printenv`, `set`, `export`, `git config --list`,
`cat *.env*` and the PowerShell `env:` drive. Independently, the runner strips
its own `TIDETABLE_TOKEN`, `TIDETABLE_SERVER`, `TASKERKEEPER_TOKEN` and
`TK_API_TOKEN` from the agent's environment, and `agent_env_drop` hides more by
pattern. The prompt itself forbids printing environment variables, `.env` files
and credentials.

Shell allowlists are prefix rules, and a build/test command runs code the agent
wrote. Treat an unattended branch as untrusted until reviewed.

### Adjudicator (planned)

The gray zone is a command that is neither on the allow list nor an obvious
deny. The plan is for the runner to ask an **adjudicator** before running a
non-listed command, and to log the verdict with the task.

The intended backend is `clef` on a Jev endpoint. The contract, verified against
the live service:

```
POST <TIDETABLE_DECISION_ENDPOINT>
Authorization: Bearer <TIDETABLE_DECISION_API_KEY>

{ "model": "<TIDETABLE_DECISION_MODEL>",
  "state": "<the command, plus worktree/context>",
  "questions": { "q": { "type": "choice",
                        "instructions": "is this shell command safe to run unattended?",
                        "criteria": { "allow": "...", "deny": "..." } } } }

-> { "answers": { "q": { "choice": "allow" | "deny",
                         "confidence": 0.98,
                         "probabilities": { "allow": 0.98, "deny": 0.02 } } },
     "usage": { ... } }
```

Credentials are env-only — never in `.tidetable/overnight.json`. The three names
are `TIDETABLE_DECISION_ENDPOINT`, `TIDETABLE_DECISION_API_KEY`,
`TIDETABLE_DECISION_MODEL`, and the runner generates `{env:...}` references (no
values) into the temporary `opencode.json` when an agent CLI needs them. Until
this lands, an unlisted command is simply not allowed: the static lists are the
whole model.
