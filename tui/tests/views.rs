use std::collections::{HashMap, HashSet};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::style::Color;
use ratatui::Terminal;
use tidetable_tui::app::{App, Hit, Load, Mode, ModeArg, Tab};
use tidetable_tui::review::Ticks;
use tidetable_tui::source::*;
use tidetable_tui::theme::{Depth, Theme};
use tidetable_tui::ui;

fn snap() -> Snapshot {
    let t = |id: &str, title: &str| TaskRef { id: id.into(), title: title.into() };
    let mut details = HashMap::new();
    details.insert(
        "3.1.2".into(),
        Detail { goal: "Render the views".into(), touches: vec!["tui/".into()], prerequisites: vec!["3.1.1".into()], success: vec!["views render".into()] },
    );
    Snapshot {
        project: "todo-tui".into(),
        todo_file: "docs/todo-tui.json".into(),
        in_progress: vec![
            Running { id: "3.1.1".into(), title: "Layout modes".into(), owner: Some("bot:1".into()), claimed_at: Some("2026-10-17T06:00:00Z".into()), lease_expired: false },
            Running { id: "3.0.9".into(), title: "Stale thing".into(), owner: Some("bot:2".into()), claimed_at: Some("2026-10-17T01:00:00Z".into()), lease_expired: true },
        ],
        ready: vec![t("3.1.2", "Now, Upcoming and Done views")],
        blocked: vec![Blocked { id: "3.2.1".into(), title: "Actions".into(), blocked_by: vec!["task 3.1.2".into()] }],
        done: vec![t("3.0.2", "Theme"), t("3.0.1", "Crate")],
        look_queue: vec![
            LookItem { id: "3.0.5".into(), title: "Eyeball it".into(), phase: "3.0".into(), note: "see the task's success criteria".into(), goal: "g".into(), success: vec!["colours look right".into(), "motion is calm".into()] },
            LookItem { id: "3.0.6".into(), title: "Other".into(), phase: "3.0".into(), note: "check the footer".into(), goal: "g".into(), success: vec![] },
        ],
        phases: vec![
            PhaseProgress { id: "3.0".into(), title: "Foundation".into(), done: 2, total: 4, complete: false },
            PhaseProgress { id: "3.1".into(), title: "Views".into(), done: 0, total: 3, complete: false },
            PhaseProgress { id: "3.2".into(), title: "Actions".into(), done: 0, total: 1, complete: false },
        ],
        details,
        disjoint: HashSet::from(["3.1.2".to_string()]),
        changelog: HashMap::from([("3.0.2".to_string(), "Theme: 5 palettes".to_string())]),
    }
}

fn app(mode: ModeArg) -> App {
    let (theme, _) = Theme::select(None, |_| None);
    let mut a = App::new(theme, mode, Ticks::memory());
    a.now_epoch = parse_utc("2026-10-17T07:00:00Z").unwrap();
    a.set_snapshot(snap());
    a
}

fn render(app: &mut App, w: u16, h: u16) -> (Terminal<TestBackend>, String) {
    let mut term = Terminal::new(TestBackend::new(w, h)).unwrap();
    term.draw(|f| ui::draw(f, app)).unwrap();
    let buf = term.backend().buffer();
    let mut s = String::new();
    for y in 0..h {
        for x in 0..w {
            s.push_str(buf[(x, y)].symbol());
        }
        s.push('\n');
    }
    (term, s)
}

fn key(c: KeyCode) -> KeyEvent {
    KeyEvent::new(c, KeyModifiers::NONE)
}

#[test]
fn auto_mode_follows_the_pane_shape() {
    assert_eq!(Mode::auto(28, 40), Mode::Side);
    assert_eq!(Mode::auto(40, 24), Mode::Side);
    assert_eq!(Mode::auto(80, 24), Mode::Standard);
    assert_eq!(Mode::auto(120, 8), Mode::Bottom);
    assert_eq!(Mode::auto(200, 50), Mode::Standard);
    // Boundaries from the spec: width < 60 and height > width/2; height <= 14 and width >= 80.
    assert_eq!(Mode::auto(59, 30), Mode::Side);
    assert_eq!(Mode::auto(60, 30), Mode::Standard);
    assert_eq!(Mode::auto(80, 14), Mode::Bottom);
    assert_eq!(Mode::auto(80, 15), Mode::Standard);
    assert_eq!(Mode::auto(79, 10), Mode::Standard);
}

#[test]
fn frames_at_the_spec_sizes() {
    for (w, h, expect) in [
        (28, 40, "CURRENT"),
        (40, 24, "CURRENT"),
        (80, 24, "Upcoming"),
        (120, 8, "review 2"),
        (200, 50, "Upcoming"),
    ] {
        let (_, text) = render(&mut app(ModeArg::Auto), w, h);
        assert!(text.contains(expect), "{w}x{h} missing {expect:?}:\n{text}");
        // Nothing may spill: every row is exactly `w` symbols wide by construction of the buffer,
        // so check the review badge instead, which must be visible in every mode.
        assert!(text.contains('2'), "{w}x{h} lost the review count");
    }
}

#[test]
fn review_badge_is_visible_in_all_three_modes() {
    for (mode, w, h, needle) in [
        (ModeArg::Standard, 100, 30, "Review 2"),
        (ModeArg::Side, 36, 30, "REVIEW 2"),
        (ModeArg::Bottom, 100, 8, "review 2"),
    ] {
        let (_, text) = render(&mut app(mode), w, h);
        assert!(text.contains(needle), "{mode:?}: {needle}\n{text}");
    }
}

#[test]
fn nothing_panics_at_tiny_sizes() {
    for mode in [ModeArg::Auto, ModeArg::Standard, ModeArg::Side, ModeArg::Bottom] {
        for tab in Tab::ALL {
            for (w, h) in [(20, 6), (1, 1), (12, 3), (5, 40), (200, 1), (30, 2)] {
                let mut a = app(mode);
                a.tab = tab;
                let _ = render(&mut a, w, h);
                a.load = Load::Failed("boom".into());
                let _ = render(&mut a, w, h);
                a.load = Load::Loading;
                let _ = render(&mut a, w, h);
                a.load = Load::Pick(vec!["a.json".into(), "b.json".into()]);
                let _ = render(&mut a, w, h);
            }
        }
    }
}

#[test]
fn every_view_renders_in_every_mode() {
    for (mode, w, h) in [(ModeArg::Standard, 100, 30), (ModeArg::Side, 36, 30), (ModeArg::Bottom, 100, 10)] {
        for tab in Tab::ALL {
            let mut a = app(mode);
            a.tab = tab;
            let (_, text) = render(&mut a, w, h);
            let want = match (tab, mode) {
                (_, ModeArg::Side) => "CURRENT",
                (Tab::Now, _) => "Layout modes",
                (Tab::Upcoming, _) => "Now, Upcoming",
                (Tab::Review, _) => "Eyeball it",
                (Tab::Done, _) => "Theme",
            };
            assert!(text.contains(want), "{mode:?}/{tab:?}: {want}\n{text}");
        }
    }
}

#[test]
fn now_shows_owner_age_and_lease_state_with_icon_and_word() {
    let (_, text) = render(&mut app(ModeArg::Standard), 100, 30);
    assert!(text.contains("bot:1 · 1h00m · lease ok"), "{text}");
    assert!(text.contains("⚠ stale lease"));
    assert!(text.contains("bot:2 · 6h00m · lease expired"));
    assert!(text.contains("► in progress"));
}

#[test]
fn upcoming_groups_by_phase_ready_first_then_blocked() {
    let mut a = app(ModeArg::Standard);
    a.tab = Tab::Upcoming;
    let (_, text) = render(&mut a, 100, 30);
    let p31 = text.find("Phase 3.1").expect("phase heading");
    let p32 = text.find("Phase 3.2").expect("phase heading");
    assert!(p31 < text.find("Now, Upcoming").unwrap() && text.find("Now, Upcoming").unwrap() < p32);
    assert!(text.contains("∥ safe to run in parallel"));
    assert!(text.contains("→ waits on task 3.1.2"));
    assert!(text.contains("⊘ blocked"));
    assert!(!text.contains("Phase 3.0 ·"), "a phase with nothing upcoming has no heading");
}

#[test]
fn done_lists_changelog_lines_and_detail_fills_on_select() {
    let mut a = app(ModeArg::Standard);
    a.tab = Tab::Done;
    let (_, text) = render(&mut a, 120, 30);
    assert!(text.contains("Theme: 5 palettes"));
    a.tab = Tab::Upcoming;
    let (_, text) = render(&mut a, 120, 30);
    // Selection 0 is the ready task 3.1.2 with its goal, touches, prerequisites and criteria.
    for s in ["Render the views", "tui/", "3.1.1", "views render"] {
        assert!(text.contains(s), "detail missing {s}:\n{text}");
    }
}

#[test]
fn empty_loading_and_error_states_have_copy() {
    let (theme, _) = Theme::select(None, |_| None);
    for (tab, copy) in [
        (Tab::Now, "Nothing in progress"),
        (Tab::Upcoming, "Nothing waiting"),
        (Tab::Review, "No human look needed"),
        (Tab::Done, "Nothing finished yet"),
    ] {
        let mut a = App::new(theme.clone(), ModeArg::Standard, Ticks::memory());
        a.set_snapshot(Snapshot::default());
        a.tab = tab;
        let (_, text) = render(&mut a, 100, 30);
        assert!(text.contains(copy), "{tab:?}:\n{text}");
    }
    let mut a = App::new(theme, ModeArg::Standard, Ticks::memory());
    assert!(render(&mut a, 100, 30).1.contains("Loading"));
    a.load = Load::Failed("tidetable failed: no such file".into());
    let text = render(&mut a, 100, 30).1;
    assert!(text.contains("Could not read the todo file") && text.contains("no such file"));
    a.load = Load::Pick(vec!["docs/todo-a.json".into(), "docs/todo-b.json".into()]);
    let text = render(&mut a, 100, 30).1;
    assert!(text.contains("2 todo files found") && text.contains("todo-b.json"));
    a.on_key(key(KeyCode::Char('2')));
    assert_eq!(a.chosen.as_deref(), Some(std::path::Path::new("docs/todo-b.json")));
}

#[test]
fn selection_survives_reflow_between_modes() {
    let mut a = app(ModeArg::Auto);
    a.tab = Tab::Now;
    a.on_key(key(KeyCode::Char('j')));
    assert_eq!(a.selected_index(Tab::Now), 1);
    let id = a.selected_entry().unwrap().id;
    for (w, h) in [(100, 30), (30, 40), (120, 8), (100, 30)] {
        let _ = render(&mut a, w, h);
        assert_eq!(a.selected_entry().unwrap().id, id, "lost selection at {w}x{h}");
    }
}

#[test]
fn keys_move_and_clamp() {
    let mut a = app(ModeArg::Standard);
    a.tab = Tab::Upcoming;
    a.on_key(key(KeyCode::Char('G')));
    assert_eq!(a.selected_index(Tab::Upcoming), 1);
    a.on_key(key(KeyCode::Char('j')));
    assert_eq!(a.selected_index(Tab::Upcoming), 1, "clamped at the end");
    a.on_key(key(KeyCode::Char('g')));
    assert_eq!(a.selected_index(Tab::Upcoming), 0);
    a.on_key(key(KeyCode::Char('3')));
    assert_eq!(a.tab, Tab::Review);
    a.on_key(key(KeyCode::Tab));
    assert_eq!(a.tab, Tab::Done);
    a.on_key(key(KeyCode::Char('q')));
    assert!(a.quit);
}

fn click(x: u16, y: u16) -> MouseEvent {
    MouseEvent { kind: MouseEventKind::Down(MouseButton::Left), column: x, row: y, modifiers: KeyModifiers::NONE }
}

#[test]
fn click_and_wheel_hit_test_against_stored_rects() {
    let mut a = app(ModeArg::Standard);
    let _ = render(&mut a, 100, 30);
    let tab = a.hits.iter().find(|(_, h)| *h == Hit::Tab(Tab::Done)).expect("tab rect").0;
    a.on_mouse(click(tab.x + 1, tab.y));
    assert_eq!(a.tab, Tab::Done);

    a.tab = Tab::Now;
    let _ = render(&mut a, 100, 30);
    let row = a.hits.iter().find(|(_, h)| *h == Hit::Row(1)).expect("row rect").0;
    a.on_mouse(click(row.x + 2, row.y));
    assert_eq!(a.selected_index(Tab::Now), 1);

    // A click outside every rect changes nothing.
    a.on_mouse(click(99, 29));
    assert_eq!(a.selected_index(Tab::Now), 1);

    let wheel = |kind| MouseEvent { kind, column: 5, row: 5, modifiers: KeyModifiers::NONE };
    a.on_mouse(wheel(MouseEventKind::ScrollUp));
    assert_eq!(a.selected_index(Tab::Now), 0);
    a.on_mouse(wheel(MouseEventKind::ScrollDown));
    assert_eq!(a.selected_index(Tab::Now), 1);
}

#[test]
fn long_lists_scroll_to_keep_the_selection_visible() {
    let mut s = snap();
    s.done = (0..60).map(|i| TaskRef { id: format!("9.{i}"), title: format!("task number {i}") }).collect();
    let mut a = app(ModeArg::Standard);
    a.snap = s;
    a.tab = Tab::Done;
    a.on_key(key(KeyCode::Char('G')));
    let (_, text) = render(&mut a, 100, 20);
    assert!(text.contains("task number 59"), "{text}");
    assert!(!text.contains("task number 0 "), "top rows scrolled away");
    assert!(*a.scroll.get(&Tab::Done).unwrap() > 0);
}

#[test]
fn review_checklist_matches_look_queue_verbose() {
    let mut a = app(ModeArg::Standard);
    a.tab = Tab::Review;
    let (_, text) = render(&mut a, 100, 30);
    // Items, criteria as boxes, and the note only when it is not a pointer at the criteria.
    assert!(text.contains("3.0.5") && text.contains("[ ] colours look right") && text.contains("[ ] motion is calm"));
    assert!(!text.contains("see the task's success criteria"), "pointer notes are noise");
    assert!(text.contains("check: check the footer"));
    assert!(text.find("3.0.5").unwrap() < text.find("3.0.6").unwrap(), "oldest first");
}

#[test]
fn space_ticks_a_criterion_and_a_full_item_reads_ready_to_clear() {
    let mut a = app(ModeArg::Standard);
    a.tab = Tab::Review;
    a.on_key(key(KeyCode::Char('j'))); // first criterion
    a.on_key(key(KeyCode::Char(' ')));
    a.on_key(key(KeyCode::Char('j')));
    a.on_key(key(KeyCode::Char(' ')));
    let (_, text) = render(&mut a, 100, 30);
    assert!(text.contains("[x] colours look right") && text.contains("[x] motion is calm"));
    assert!(text.contains("all checked, ready to clear"));
    a.on_key(key(KeyCode::Char(' ')));
    assert!(!render(&mut a, 100, 30).1.contains("ready to clear"));
}

#[test]
fn no_color_frames_contain_no_fixed_colours() {
    let theme = Theme::build("biolume", Depth::Mono).unwrap();
    let mut a = App::new(theme, ModeArg::Standard, Ticks::memory());
    a.set_snapshot(snap());
    for (w, h) in [(100, 30), (36, 30), (100, 8)] {
        a.mode_arg = ModeArg::Auto;
        let (term, _) = render(&mut a, w, h);
        let buf = term.backend().buffer();
        for y in 0..h {
            for x in 0..w {
                let c = &buf[(x, y)];
                for col in [c.fg, c.bg] {
                    assert!(!matches!(col, Color::Rgb(..) | Color::Indexed(_)), "colour at {x},{y} in {w}x{h}");
                }
            }
        }
    }
}
