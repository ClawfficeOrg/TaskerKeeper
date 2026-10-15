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

Every command is decided by lists in `tidetable/tkrun.py`, passed to
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

### The gray zone: adjudicated or prompted

A command that is neither on the allow list nor on the deny list is the gray
zone. It is settled in exactly one of two ways, never silently run:

- **Adjudicator on** (`adjudicator.enabled`: it decides). The decision service
  below judges the command; allow runs it, deny blocks it.
- **Adjudicator off** (nobody can decide unattended: the person is prompted).
  opencode is told `bash: "*": "ask"` and `claude -p` keeps the asking
  permission mode, so the harness raises the command to whoever is watching.
  An unattended run with no watcher therefore does not quietly execute it.

The allow and deny lists are checked first and are unchanged: a listed command
never reaches either path.

### Adjudicator

When `adjudicator.enabled` is set, the runner starts a small **decision service**
on loopback and the agent's hook asks it before running a bash/shell command. The
allow and deny lists are checked first (deny wins), so a listed command never
costs a model call; only a genuinely unlisted command reaches the model.

The service holds the credentials; the agent only ever learns a `host:port` (from
`TIDETABLE_ADJUDICATOR_HOST`/`_PORT` in its environment, or the
`.tkrun/adjudicator.json` discovery file).

The verdict comes from a decision model — `clef` on a Jev endpoint. The contract,
verified against the live service:

```
POST <TIDETABLE_DECISION_ENDPOINT>
Authorization: Bearer <TIDETABLE_DECISION_API_KEY>

{ "model": "<TIDETABLE_DECISION_MODEL>",
  "state": "<the command, plus the worktree>",
  "questions": { "q": { "type": "choice",
                        "instructions": "is this shell command safe to run unattended?",
                        "criteria": { "allow": "...", "deny": "..." } } } }

-> { "answers": { "q": { "choice": "allow" | "deny",
                         "confidence": 0.98,
                         "probabilities": { "allow": 0.98, "deny": 0.02 } } },
     "usage": { ... } }
```

Sampled verdicts: `cargo test --workspace` → allow 0.98, `git diff --stat` →
allow 0.99, `rm -rf / --no-preserve-root` → deny 0.98, `git push --force` → deny
0.99, `cat .env` → deny 0.94.

It **fails closed**: a missing endpoint, key or model, a network error, an
unparseable reply, an unexpected verdict, or a confidence below
`adjudicator.min_confidence` all resolve to deny. An `allow` whose probability
mass on `deny` exceeds `1 - confidence` is also treated as deny.

Wiring, per agent CLI:

- **opencode** — the generated `opencode.json` registers
  `integrations/opencode/tk-adjudicator.ts`, which uses the `execute.before` hook:
  it asks the service and **throws** on a non-allow answer, aborting the tool.
  Fail-closed there too: no service address, a dead service or a timeout all
  throw. `bash` stays `allow`, so an allowed command is not prompted (the same
  mechanism the `rtk` plugin relies on).
- **claude** — the static `--allowedTools`/`--disallowedTools` lists still apply;
  a runtime hook for claude is not wired yet, so its gray zone falls back to the
  static lists.

Every verdict is appended to `<run dir>/adjudicator.jsonl` and echoed to the log.
Try one command without a full run:

```bash
tidetable tkrun adjudicate docs/todo.json "rm -rf /"
```

Config (`adjudicator` in `.tidetable/tkrun.json`): `enabled`,
`min_confidence` (default 0.6), `timeout_seconds` (15), and the three `*_env`
names. `tkrun run --adjudicate` / `--no-adjudicate` overrides `enabled` per run.
Credentials are env-only — never in the config file. Until this is on, an
unlisted command is not silently run: opencode asks (`bash: "*": "ask"`) and
`claude -p` keeps the asking permission mode, so the person decides.
