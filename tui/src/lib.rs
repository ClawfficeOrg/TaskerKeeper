//! TideTable TUI: data layer first, chrome later.
//!
//! The crate never writes a todo file. AGENTS.md makes the CLI (or the hub) the
//! single writer, so every mutation goes through a `Source`, never `File::create`.

pub mod source;
pub mod theme;

pub use source::{
    build_snapshot, parse_utc, resolve_todo, Blocked, CliRunner, LocalSource, LookItem,
    PhaseProgress, Runner, Running, Snapshot, Source, SourceError, TaskRef, TODO_SEARCH_DIRS,
};
