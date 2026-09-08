What it is
JSON todo format + Python CLI + jq/bash ralph variant for agent task management. Stable IDs, prereq DAG, parallel groups, semver milestone mapping. v0.1, 4 commits, clean tree, main in sync with origin.

Works (verified live)
validate ✓ both examples pass schema
next ✓ DAG gating real: simple-project correctly blocks on in_progress 1.0.2/1.0.3; zoidmatter picks 7.0.1
parallel, deps ✓ sensible output
Windows UTF-8 fix is real — ran it here, glyphs render
Honest docs: docs/memory.md "Known Gaps" matches code exactly. Rare.
Bugs / latent defects
Non-editable install breaks validate. SCHEMA_PATH = Path(__file__).parent.parent / "schema" (cli.py:34) — in site-packages that's site-packages/schema/, doesn't exist. Only pip install -e . works. Fix: ship schema as package data, load via importlib.resources.
Two implementations of same logic. Python CLI and ralph-json.sh diverge: Python sorts candidates by (prereq_count, id); jq takes first in doc order. jq done warns on unmet prereqs; Python mark_done doesn't check at all. Will drift. Make the sh a thin wrapper around the CLI.
Phase-level prerequisites not enforced. find_next only checks task prereqs. Phase 1.1 requires phase "1.0", but a task in 1.1 with no task-level prereqs gets picked with 1.0 half-done. Examples hide it (task prereqs happen to cover it).
in_progress is a dead state. No start command sets it; next never returns in_progress tasks; only done unblocks. Crashed session = orphaned in_progress task, silently blocks downstream forever, never resumed. Needs start + resume-priority in next.
validate is shape-only. Dangling prereq ID (task blocks forever, silently), cycles, duplicate task IDs, task ID prefix ≠ phase ID — all pass. Cheap to add: 4 checks in validate_todo.
ralph-json.sh specifics (couldn't run it — no jq on this box):
Line 57 select(.prerequisites | length > 0 or . == null or . == []) is a tautology — always true
Line 70 join(", ") // "none" — "" is truthy in jq, prints [deps: ] not [deps: none]
Empty done-list yields [""] not [] (harmless today)
Schema claims unimplemented things. changelog_entries "auto-collected" — no code does it. moved/cancelled statuses have no CLI path.
Gaps vs. stated goal
No "ready list" command. parallel_group is display-only; next returns one task. For parallel agents, ready (all currently-runnable) is the core value prop — missing.
No --json output. Agent-facing tool whose only output is human text with box-drawing chars. ralph would parse that.
convert (markdown↔JSON) promised in README/philosophy, not implemented.
No tests, no CI.
No atomic writes / locking: jq version tmp+rename, Python overwrites in-place. Two agents = lost update.
add has no flags for prereqs/complexity/agent/parallel_group; crashes on malformed existing IDs (int(t["id"].split(".")[-1]), cli.py:202).
Nits
README "Project Structure" block stale: says convert exists, omits taskerkeeper/ package and pyproject.toml
philosophy.md: "Python 3 standard library only" — pyproject requires jsonschema
Schema agent enum has flagship; agent_config.tiers defines only three tiers
_phase_id in examples: redundant denormalization, not in schema
CLI writes +00:00 timestamps, examples use Z
cli.py:94 comment says "unsatisfied prereqs" — sorts by total prereqs (all candidates have zero unsatisfied)
Verdict
Solid scaffold, clear problem, docs match code. Priorities if I were you:

Package data for schema (one-line-ish fix, unbricks pip install .)
start + resume in next + ready command (the actual pitch: parallel agents)
Semantic validation (dangling/cycle/dupes/phase-consistency)
Collapse ralph-json.sh to wrapper around CLI
Minimal test suite + CI