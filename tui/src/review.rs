//! Local tick marks for the human review checklist.
//!
//! Ticks are a reading aid, not tidetable state, so they live in a sidecar under
//! `.tidetable/` and never in the todo file (AGENTS.md: the CLI is the only todo writer).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::Value;

pub const SIDECAR: &str = ".tidetable/review-ticks.json";

/// project -> task id -> one bool per success criterion.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Ticks {
    path: Option<PathBuf>,
    map: BTreeMap<String, BTreeMap<String, Vec<bool>>>,
}

impl Ticks {
    /// In-memory only; tests and `--read-only` runs use this.
    pub fn memory() -> Self {
        Self::default()
    }

    /// Load `<base>/.tidetable/review-ticks.json`; a missing or damaged file starts empty.
    pub fn load(base: &Path) -> Self {
        let path = base.join(SIDECAR);
        let map = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();
        Self { path: Some(path), map }
    }

    pub fn get(&self, project: &str, task: &str, idx: usize) -> bool {
        self.map.get(project).and_then(|t| t.get(task)).and_then(|v| v.get(idx)).copied().unwrap_or(false)
    }

    /// True when the task has criteria and every one is ticked.
    pub fn all(&self, project: &str, task: &str, total: usize) -> bool {
        total > 0 && (0..total).all(|i| self.get(project, task, i))
    }

    pub fn toggle(&mut self, project: &str, task: &str, idx: usize, total: usize) {
        let v = self.map.entry(project.into()).or_default().entry(task.into()).or_default();
        v.resize(total.max(idx + 1), false);
        v[idx] = !v[idx];
        self.save();
    }

    /// Best effort: a failed save costs the user a tick on restart, not a crash.
    fn save(&self) {
        let Some(path) = &self.path else { return };
        let Ok(text) = serde_json::to_string_pretty(&self.map) else { return };
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let tmp = path.with_extension("json.tmp");
        if std::fs::write(&tmp, text).is_ok() {
            let _ = std::fs::rename(&tmp, path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ticks_persist_through_the_sidecar() {
        let dir = std::env::temp_dir().join(format!("tt-ticks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mut t = Ticks::load(&dir);
        assert!(!t.get("p", "1.1", 0));
        t.toggle("p", "1.1", 1, 3);
        assert!(t.get("p", "1.1", 1) && !t.all("p", "1.1", 3));
        t.toggle("p", "1.1", 0, 3);
        t.toggle("p", "1.1", 2, 3);
        let again = Ticks::load(&dir);
        assert!(again.all("p", "1.1", 3));
        // The sidecar sits under .tidetable/, never beside a todo file.
        assert!(dir.join(SIDECAR).is_file());
        t.toggle("p", "1.1", 0, 3);
        assert!(!Ticks::load(&dir).all("p", "1.1", 3));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn damaged_sidecar_starts_empty() {
        let dir = std::env::temp_dir().join(format!("tt-ticks-bad-{}", std::process::id()));
        std::fs::create_dir_all(dir.join(".tidetable")).unwrap();
        std::fs::write(dir.join(SIDECAR), "{not json").unwrap();
        assert!(!Ticks::load(&dir).get("p", "1", 0));
        let _ = std::fs::remove_dir_all(dir);
    }
}
