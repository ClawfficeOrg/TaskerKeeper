//! Restrained motion: one short pulse on the id of a row whose status just changed.
//!
//! No animation crate (tachyonfx was considered and not added: a 600 ms colour pulse on one
//! span does not justify a dependency). Off under `TIDETABLE_REDUCE_MOTION` and `NO_COLOR`;
//! the main loop only wakes faster while a pulse is live, so an idle TUI draws nothing.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use crate::source::Snapshot;

pub const PULSE: Duration = Duration::from_millis(600);

/// Motion is opt-out: any non-empty, non-"0" reduce flag or a NO_COLOR present turns it off.
pub fn enabled(env: impl Fn(&str) -> Option<String>) -> bool {
    let reduce = env("TIDETABLE_REDUCE_MOTION").is_some_and(|v| !v.is_empty() && v != "0");
    let no_color = env("NO_COLOR").is_some_and(|v| !v.is_empty());
    !(reduce || no_color)
}

/// id -> where it sits (a lane), so a move between lanes counts as a change.
pub fn lanes(s: &Snapshot) -> HashMap<String, &'static str> {
    let mut m = HashMap::new();
    for t in &s.done {
        m.insert(t.id.clone(), "done");
    }
    for t in &s.ready {
        m.insert(t.id.clone(), "ready");
    }
    for t in &s.blocked {
        m.insert(t.id.clone(), "blocked");
    }
    for t in &s.look_queue {
        m.insert(t.id.clone(), "look");
    }
    for t in &s.in_progress {
        m.insert(t.id.clone(), "running");
    }
    m
}

/// Ids whose lane differs from `before`; tasks that first appear are not "changed".
pub fn changed(before: &HashMap<String, &'static str>, after: &Snapshot) -> HashSet<String> {
    lanes(after)
        .into_iter()
        .filter(|(id, lane)| before.get(id).is_some_and(|b| b != lane))
        .map(|(id, _)| id)
        .collect()
}

#[derive(Debug, Default)]
pub struct Pulses {
    ids: HashSet<String>,
    until: Option<Instant>,
}

impl Pulses {
    pub fn start(&mut self, ids: HashSet<String>, now: Instant) {
        if !ids.is_empty() {
            self.ids = ids;
            self.until = Some(now + PULSE);
        }
    }

    pub fn active(&self, id: &str, now: Instant) -> bool {
        self.until.is_some_and(|u| now < u) && self.ids.contains(id)
    }

    /// True while a frame is still owed; the main loop uses this to wake faster, then stop.
    pub fn live(&self, now: Instant) -> bool {
        self.until.is_some_and(|u| now < u)
    }

    /// One more repaint is owed after the pulse ends, to clear it.
    pub fn expire(&mut self, now: Instant) -> bool {
        match self.until {
            Some(u) if now >= u => {
                self.until = None;
                self.ids.clear();
                true
            }
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source::TaskRef;

    fn snap(ready: &[&str], done: &[&str]) -> Snapshot {
        let t = |id: &&str| TaskRef {
            id: (*id).into(),
            title: String::new(),
        };
        Snapshot {
            ready: ready
                .iter()
                .map(|i| crate::source::TaskRef {
                    id: (*i).into(),
                    title: String::new(),
                })
                .collect(),
            done: done.iter().map(t).collect(),
            ..Snapshot::default()
        }
    }

    #[test]
    fn reduced_motion_and_no_color_disable_effects() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| {
                pairs
                    .iter()
                    .find(|(n, _)| *n == k)
                    .map(|(_, v)| v.to_string())
            }
        };
        assert!(enabled(env(&[])));
        assert!(!enabled(env(&[("TIDETABLE_REDUCE_MOTION", "1")])));
        assert!(!enabled(env(&[("NO_COLOR", "1")])));
        assert!(enabled(env(&[("TIDETABLE_REDUCE_MOTION", "0")])));
    }

    #[test]
    fn only_lane_changes_pulse_and_new_rows_do_not() {
        let before = lanes(&snap(&["1.1", "1.2"], &[]));
        let after = snap(&["1.2", "1.9"], &["1.1"]);
        assert_eq!(changed(&before, &after), HashSet::from(["1.1".to_string()]));
    }

    #[test]
    fn a_pulse_lasts_600ms_then_clears_once() {
        let t0 = Instant::now();
        let mut p = Pulses::default();
        p.start(HashSet::from(["1.1".to_string()]), t0);
        assert!(p.active("1.1", t0 + Duration::from_millis(599)) && !p.active("1.2", t0));
        assert!(!p.active("1.1", t0 + PULSE) && !p.live(t0 + PULSE));
        assert!(
            p.expire(t0 + PULSE) && !p.expire(t0 + PULSE),
            "exactly one clearing repaint"
        );
        let mut q = Pulses::default();
        q.start(HashSet::new(), t0);
        assert!(!q.live(t0), "nothing to pulse, nothing to wake for");
    }
}
