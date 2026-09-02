# TaskerKeeper Memory

Project state and decisions for agents. Read before starting work.

## What This Is

Structured JSON task management for autonomous agents. Alternative to markdown
todo files. Stable IDs, dependency DAG, parallel execution groups, semver
version mapping. Built for [ralph](https://github.com/ClawfficeOrg/Zoid), works
with any autonomous agent. See `docs/philosophy.md` for rationale.

## Layout

```
schema/todo-v1.schema.json    JSON Schema (draft 2020-12)
taskerkeeper/cli.py           CLI logic (package)
scripts/taskerkeeper.py       Thin shim -> taskerkeeper.cli.main
ralph/ralph-json.sh           JSON-aware ralph variant (bash/jq)
skills/taskerkeeper/SKILL.md  Hermes skill
docs/                         philosophy, integration guide, memory
examples/                     simple-project, zoidmatter-v7
pyproject.toml                setuptools packaging, console script `taskerkeeper`
```

## Current State (2026-09-02)

- Scaffold shipped (Aug 27 2026), then hardened in a working session.
- CLI now runs on Windows: stdout/stderr forced UTF-8 (`errors="replace"`).
  Previously crashed with `UnicodeEncodeError` on cp1252 consoles.
- Installable: `pip install -e .` pulls `jsonschema`, registers `taskerkeeper`
  command. `python scripts/taskerkeeper.py ...` still works via shim.
- Both example files validate against the schema end-to-end.
- All four core commands exercised against both examples: `validate`, `next`,
  `done`, `add`. DAG behavior verified (blocked prereqs -> none; pick after
  prereqs done).

## Decisions

- **Changelog is nullable.** Task `changelog` allows `null` = "no entry".
  Schema uses `["string", "null"]`. The zoidmatter-v7 example (task 7.1.2,
  the merge task) relies on this.
- **Files read/written as UTF-8** in the CLI (`encoding="utf-8"`,
  `ensure_ascii=False`). Do not strip to ASCII.
- **Editable install is the supported install path.** README documents
  `pip install -e .`. Wheel packaging (bundling schema as package data) not
  done yet — SCHEMA_PATH resolves to repo root.
- CLI glyphs are intentional (`✓ ✗ ► · → ═ ─ •`). Keep them; the UTF-8
  reconfigure handles rendering.

## Known Gaps / Next

- `ralph/ralph-json.sh` and the Hermes skill are written but not end-to-end
  tested against the CLI.
- No tests. No CI validation hook (see philosophy.md "Future Directions").
- `convert` (markdown <-> JSON) mentioned in README structure but not
  implemented in the CLI. Docs point this out; migration is manual.