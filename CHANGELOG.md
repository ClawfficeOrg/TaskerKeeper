# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `pyproject.toml` — installable via `pip install -e .`; pulls `jsonschema`
  and registers a `taskerkeeper` console command.
- `taskerkeeper/` package — CLI logic lives at `taskerkeeper/cli.py`;
  `scripts/taskerkeeper.py` remains as a shim.
- `docs/memory.md` — project state and decisions for agents.
- `CHANGELOG.md` — this file.

### Fixed

- CLI crashed with `UnicodeEncodeError` on Windows (cp1252 console) printing
  checkmark glyphs. stdout/stderr now reconfigured to UTF-8 with
  `errors="replace"`; files are read/written as UTF-8.
- Schema rejected task `changelog: null`. The field now allows
  `["string", "null"]`, so the zoidmatter-v7 example validates.
- Docs aligned with the actual CLI — removed references to unimplemented
  `status`, `convert-md`, `convert-json`, and `add-task` commands and the
  positional-phase `add` form.

### Changed

- First verified state: both example files pass schema validation, and
  `validate` / `next` / `done` / `add` are exercised end-to-end against both.