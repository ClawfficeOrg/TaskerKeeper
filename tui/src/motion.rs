//! Restrained motion: one short pulse on the id of a row whose status just changed.
//!
//! Built on tachyonfx (shared with the other clawffice apps): a colour fade on the row whose
//! lane just changed. Off under `TIDETABLE_REDUCE_MOTION` and `NO_COLOR`; the main loop only
//! wakes faster while an effect is live, so an idle TUI draws nothing.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;
use tachyonfx::{fx, EffectManager, Interpolation};

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

/// Motion engine: a tachyonfx [`EffectManager`] run over the frame buffer after the widgets
/// are drawn. Idle means no effects, so `live()` is false and the loop blocks.
#[derive(Default)]
pub struct Motion {
    mgr: EffectManager<String>,
    queued: HashSet<String>,
    last: Option<Instant>,
}

impl std::fmt::Debug for Motion {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Motion")
            .field("queued", &self.queued)
            .field("live", &self.live())
            .finish()
    }
}

impl Motion {
    /// Ids whose row should flash on the next frame.
    pub fn queue(&mut self, ids: HashSet<String>) {
        self.queued.extend(ids);
    }

    pub fn pending(&self) -> bool {
        !self.queued.is_empty()
    }

    /// True while a frame is still owed; the main loop uses this to wake faster, then stop.
    pub fn live(&self) -> bool {
        self.pending() || self.mgr.is_running()
    }

    /// Start effects for queued ids that are on screen (`rows`), then advance all effects.
    pub fn run(
        &mut self,
        buf: &mut Buffer,
        area: Rect,
        rows: &[(Rect, String)],
        accent: Color,
        now: Instant,
    ) {
        for (rect, id) in rows {
            if self.queued.remove(id) {
                let fx =
                    fx::fade_from_fg(accent, (PULSE.as_millis() as u32, Interpolation::QuadOut))
                        .with_area(*rect);
                self.mgr.add_unique_effect(id.clone(), fx);
            }
        }
        // Whatever is off screen has nothing to flash.
        self.queued.clear();
        let dt = self
            .last
            .map_or(Duration::ZERO, |l| now.saturating_duration_since(l));
        self.mgr.process_effects(dt.into(), buf, area);
        self.last = self.mgr.is_running().then_some(now);
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
    fn a_queued_id_runs_one_effect_that_ends_within_the_pulse() {
        let t0 = Instant::now();
        let area = Rect::new(0, 0, 20, 3);
        let mut buf = Buffer::empty(area);
        let rows = vec![(Rect::new(0, 1, 20, 1), "1.1".to_string())];
        let mut m = Motion::default();
        assert!(!m.live());
        m.queue(HashSet::from(["1.1".to_string()]));
        assert!(m.live());
        m.run(&mut buf, area, &rows, Color::Red, t0);
        assert!(m.live(), "effect started");
        m.run(
            &mut buf,
            area,
            &rows,
            Color::Red,
            t0 + PULSE + Duration::from_millis(50),
        );
        assert!(!m.live(), "effect ended, loop may block again");
    }

    #[test]
    fn an_id_that_is_off_screen_owes_nothing() {
        let area = Rect::new(0, 0, 20, 3);
        let mut buf = Buffer::empty(area);
        let mut m = Motion::default();
        m.queue(HashSet::from(["9.9".to_string()]));
        m.run(&mut buf, area, &[], Color::Red, Instant::now());
        assert!(!m.live());
    }
}
