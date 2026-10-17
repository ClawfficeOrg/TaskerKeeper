//! True-colour theme from the five vendored clawffice-design palettes.
//!
//! Every render goes through [`Theme`]; no widget names a `Color` itself. State is never
//! colour alone (suite rule 8), so [`Status`] always carries an icon and a word.

use ratatui::style::{Color, Modifier, Style};
use serde_json::Value;

pub const DEFAULT_PALETTE: &str = "biolume";

/// (name, json) for each palette, embedded so the binary needs no data files.
pub const PALETTES: [(&str, &str); 5] = [
    ("biolume", include_str!("../palettes/biolume.json")),
    ("deep-tide", include_str!("../palettes/deep-tide.json")),
    ("sea-glass", include_str!("../palettes/sea-glass.json")),
    ("phosphor", include_str!("../palettes/phosphor.json")),
    ("boiled", include_str!("../palettes/boiled.json")),
];

/// Tidetable product hue `tide-300` (core.json `color.product`), picked by palette scheme.
const TIDE_300_DARK: (u8, u8, u8) = (0x8C, 0xC4, 0xF0);
const TIDE_300_LIGHT: (u8, u8, u8) = (0x2F, 0x6F, 0xA8);

/// How many colours the terminal can show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Depth {
    TrueColor,
    Ansi256,
    /// `NO_COLOR` or `TERM=dumb`: no colour at all, only modifiers and glyphs.
    Mono,
}

impl Depth {
    /// `NO_COLOR` (any non-empty value, per no-color.org) and `TERM=dumb` win over everything.
    pub fn detect(get: impl Fn(&str) -> Option<String>) -> Self {
        if get("NO_COLOR").is_some_and(|v| !v.is_empty()) || get("TERM").as_deref() == Some("dumb") {
            return Self::Mono;
        }
        let truecolor = get("COLORTERM").is_some_and(|v| v == "truecolor" || v == "24bit");
        // Windows Terminal sets WT_SESSION and does 24-bit without advertising COLORTERM.
        if truecolor || get("WT_SESSION").is_some() {
            Self::TrueColor
        } else {
            Self::Ansi256
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Theme {
    pub name: String,
    pub light: bool,
    pub depth: Depth,
    pub bg: Color,
    pub surface: Color,
    pub surface_raised: Color,
    pub border: Color,
    pub border_focus: Color,
    pub text: Color,
    pub text_dim: Color,
    pub text_strong: Color,
    /// Tidetable product hue: focus, selection, the highlighted syllable of the wordmark.
    pub accent: Color,
    /// Claw orange or the palette's second colour; never the first colour (suite rule 2).
    pub second: Color,
    pub success: Color,
    pub warn: Color,
    pub danger: Color,
    pub info: Color,
}

fn hex(s: &str) -> Option<(u8, u8, u8)> {
    let s = s.strip_prefix('#')?;
    if s.len() != 6 {
        return None;
    }
    let n = |i: usize| u8::from_str_radix(&s[i..i + 2], 16).ok();
    Some((n(0)?, n(2)?, n(4)?))
}

fn mix(a: (u8, u8, u8), b: (u8, u8, u8), t: f32) -> (u8, u8, u8) {
    let f = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    (f(a.0, b.0), f(a.1, b.1), f(a.2, b.2))
}

/// WCAG relative luminance contrast ratio between two colours.
pub fn contrast(a: (u8, u8, u8), b: (u8, u8, u8)) -> f32 {
    let lum = |c: (u8, u8, u8)| {
        let ch = |v: u8| {
            let v = v as f32 / 255.0;
            if v <= 0.03928 { v / 12.92 } else { ((v + 0.055) / 1.055).powf(2.4) }
        };
        0.2126 * ch(c.0) + 0.7152 * ch(c.1) + 0.0722 * ch(c.2)
    };
    let (la, lb) = (lum(a), lum(b));
    (la.max(lb) + 0.05) / (la.min(lb) + 0.05)
}

/// Nearest xterm-256 index: the 6x6x6 cube for colour, the grey ramp for near-greys.
pub fn to_ansi256(c: (u8, u8, u8)) -> u8 {
    const LEVELS: [u8; 6] = [0, 95, 135, 175, 215, 255];
    let near = |v: u8| (0..6).min_by_key(|&i| (LEVELS[i] as i32 - v as i32).abs()).unwrap_or(0);
    let (r, g, b) = (near(c.0), near(c.1), near(c.2));
    let cube = (LEVELS[r], LEVELS[g], LEVELS[b]);
    let avg = (c.0 as u16 + c.1 as u16 + c.2 as u16) / 3;
    let grey_i = ((avg.saturating_sub(8)) / 10).min(23) as u8;
    let grey_v = 8 + 10 * grey_i;
    let grey = (grey_v, grey_v, grey_v);
    let dist = |p: (u8, u8, u8)| {
        let d = |x: u8, y: u8| (x as i32 - y as i32).pow(2);
        d(p.0, c.0) + d(p.1, c.1) + d(p.2, c.2)
    };
    if dist(grey) < dist(cube) {
        232 + grey_i
    } else {
        (16 + 36 * r + 6 * g + b) as u8
    }
}

impl Theme {
    /// Build a theme. Unknown palette names fall back to biolume; `Err` carries the bad name
    /// so the caller can warn rather than silently showing the wrong look.
    pub fn build(palette: &str, depth: Depth) -> Result<Self, String> {
        let known = PALETTES.iter().find(|(n, _)| *n == palette);
        let (name, raw) = known.copied().unwrap_or(PALETTES[0]);
        let mut theme = Self::from_palette(name, raw, depth);
        if depth == Depth::Mono {
            theme = Self::mono(name, theme.light);
        }
        if known.is_none() {
            return Err(format!("unknown palette {palette:?}; using {DEFAULT_PALETTE}"));
        }
        Ok(theme)
    }

    /// `--palette` beats `TIDETABLE_PALETTE` beats biolume. The bool reports a bad name.
    pub fn select(
        flag: Option<&str>,
        get: impl Fn(&str) -> Option<String>,
    ) -> (Self, Option<String>) {
        let depth = Depth::detect(&get);
        let want = flag.map(String::from).or_else(|| get("TIDETABLE_PALETTE"));
        let want = want.unwrap_or_else(|| DEFAULT_PALETTE.into());
        match Self::build(&want, depth) {
            Ok(t) => (t, None),
            Err(w) => (Self::build(DEFAULT_PALETTE, depth).expect("default palette exists"), Some(w)),
        }
    }

    fn from_palette(name: &str, raw: &str, depth: Depth) -> Self {
        let v: Value = serde_json::from_str(raw).expect("vendored palette is valid JSON");
        let light = v["scheme"] == "light";
        let role = |k: &str| hex(v["role"][k].as_str().unwrap_or("#000000")).unwrap_or((0, 0, 0));
        let status = |k: &str| hex(v["status-color"][k].as_str().unwrap_or("#000000")).unwrap_or((0, 0, 0));
        let bg = role("window-background");
        let surface = role("surface");
        let border = role("border");
        let paint = |c: (u8, u8, u8)| match depth {
            Depth::TrueColor => Color::Rgb(c.0, c.1, c.2),
            Depth::Ansi256 => Color::Indexed(to_ansi256(c)),
            Depth::Mono => Color::Reset,
        };
        Self {
            name: name.into(),
            light,
            depth,
            bg: paint(bg),
            surface: paint(surface),
            surface_raised: paint(mix(surface, border, 0.5)),
            border: paint(border),
            border_focus: paint(if light { TIDE_300_LIGHT } else { TIDE_300_DARK }),
            text: paint(role("text")),
            text_dim: paint(role("text-muted")),
            text_strong: paint(role("text-strong")),
            accent: paint(if light { TIDE_300_LIGHT } else { TIDE_300_DARK }),
            second: paint(role("second")),
            success: paint(status("success")),
            warn: paint(status("warning")),
            danger: paint(status("danger")),
            info: paint(status("info")),
        }
    }

    /// No colours at all: the terminal's own foreground and background, glyphs and bold only.
    fn mono(name: &str, light: bool) -> Self {
        let c = Color::Reset;
        Self {
            name: name.into(),
            light,
            depth: Depth::Mono,
            bg: c,
            surface: c,
            surface_raised: c,
            border: c,
            border_focus: c,
            text: c,
            text_dim: c,
            text_strong: c,
            accent: c,
            second: c,
            success: c,
            warn: c,
            danger: c,
            info: c,
        }
    }

    /// True when any token is a fixed colour (used by the NO_COLOR test).
    pub fn has_colour(&self) -> bool {
        [
            self.bg, self.surface, self.surface_raised, self.border, self.border_focus, self.text,
            self.text_dim, self.text_strong, self.accent, self.second, self.success, self.warn,
            self.danger, self.info,
        ]
        .iter()
        .any(|c| !matches!(c, Color::Reset))
    }

    fn mono_mod(&self, style: Style, m: Modifier) -> Style {
        if self.depth == Depth::Mono { style.add_modifier(m) } else { style }
    }

    pub fn base(&self) -> Style {
        Style::new().fg(self.text).bg(self.bg)
    }
    pub fn panel(&self) -> Style {
        Style::new().fg(self.text).bg(self.surface)
    }
    pub fn dim(&self) -> Style {
        Style::new().fg(self.text_dim)
    }
    pub fn strong(&self) -> Style {
        self.mono_mod(Style::new().fg(self.text_strong), Modifier::BOLD)
    }
    pub fn accent_style(&self) -> Style {
        self.mono_mod(Style::new().fg(self.accent).add_modifier(Modifier::BOLD), Modifier::UNDERLINED)
    }
    /// Selected row: reversed in mono, where colour cannot carry selection.
    pub fn selected(&self) -> Style {
        if self.depth == Depth::Mono {
            Style::new().add_modifier(Modifier::REVERSED)
        } else {
            Style::new().fg(self.text_strong).bg(self.surface_raised).add_modifier(Modifier::BOLD)
        }
    }
    pub fn border_style(&self, focused: bool) -> Style {
        Style::new().fg(if focused { self.border_focus } else { self.border })
    }

    pub fn status_style(&self, s: Status) -> Style {
        let c = match s {
            Status::Pending => self.text_dim,
            Status::InProgress => self.accent,
            Status::Done => self.success,
            Status::NeedsLook => self.second,
            Status::Blocked => self.danger,
            Status::Claimed => self.info,
            Status::StaleLease => self.warn,
        };
        Style::new().fg(c)
    }

    /// Icon and word in the status colour, e.g. `► in progress`.
    pub fn status_span(&self, s: Status) -> ratatui::text::Span<'static> {
        ratatui::text::Span::styled(s.label(), self.status_style(s))
    }
}

/// Task states a view can show. Each is an icon plus a word, never colour alone.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Status {
    Pending,
    InProgress,
    Done,
    NeedsLook,
    Blocked,
    Claimed,
    StaleLease,
}

impl Status {
    pub const ALL: [Status; 7] = [
        Status::Pending,
        Status::InProgress,
        Status::Done,
        Status::NeedsLook,
        Status::Blocked,
        Status::Claimed,
        Status::StaleLease,
    ];

    /// Glyphs follow the CLI's habits (`· ► ✓ →`); the rest are chosen to be distinct shapes.
    pub fn icon(self) -> &'static str {
        match self {
            Status::Pending => "·",
            Status::InProgress => "►",
            Status::Done => "✓",
            Status::NeedsLook => "◉",
            Status::Blocked => "⊘",
            Status::Claimed => "●",
            Status::StaleLease => "⚠",
        }
    }

    pub fn word(self) -> &'static str {
        match self {
            Status::Pending => "pending",
            Status::InProgress => "in progress",
            Status::Done => "done",
            Status::NeedsLook => "needs look",
            Status::Blocked => "blocked",
            Status::Claimed => "claimed",
            Status::StaleLease => "stale lease",
        }
    }

    pub fn label(self) -> String {
        format!("{} {}", self.icon(), self.word())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rgb(c: Color) -> (u8, u8, u8) {
        match c {
            Color::Rgb(r, g, b) => (r, g, b),
            other => panic!("not rgb: {other:?}"),
        }
    }

    #[test]
    fn all_five_palettes_load_and_text_is_readable() {
        assert_eq!(PALETTES.len(), 5);
        for (name, _) in PALETTES {
            let t = Theme::build(name, Depth::TrueColor).unwrap();
            assert_eq!(t.name, name);
            let bg = rgb(t.bg);
            assert!(contrast(rgb(t.text), bg) >= 4.5, "{name} body text");
            assert!(contrast(rgb(t.text_dim), bg) >= 4.5, "{name} muted text");
            assert!(contrast(rgb(t.accent), bg) >= 3.0, "{name} accent");
        }
    }

    #[test]
    fn scheme_picks_the_product_hue() {
        let dark = Theme::build("biolume", Depth::TrueColor).unwrap();
        let light = Theme::build("sea-glass", Depth::TrueColor).unwrap();
        assert!(!dark.light && light.light);
        assert_eq!(rgb(dark.accent), TIDE_300_DARK);
        assert_eq!(rgb(light.accent), TIDE_300_LIGHT);
    }

    #[test]
    fn no_color_and_dumb_terms_have_no_colour() {
        let env = |k: &'static str, v: &'static str| move |q: &str| (q == k).then(|| v.to_string());
        for get in [env("NO_COLOR", "1"), env("TERM", "dumb")] {
            let (t, warn) = Theme::select(None, get);
            assert!(warn.is_none());
            assert_eq!(t.depth, Depth::Mono);
            assert!(!t.has_colour());
        }
        // An empty NO_COLOR does not count.
        assert_ne!(Depth::detect(|k| (k == "NO_COLOR").then(String::new)), Depth::Mono);
    }

    #[test]
    fn palette_flag_beats_env_and_bad_names_warn() {
        let get = |k: &str| (k == "TIDETABLE_PALETTE").then(|| "boiled".to_string());
        assert_eq!(Theme::select(None, get).0.name, "boiled");
        assert_eq!(Theme::select(Some("phosphor"), get).0.name, "phosphor");
        let (t, warn) = Theme::select(Some("nope"), get);
        assert_eq!(t.name, "biolume");
        assert!(warn.unwrap().contains("nope"));
    }

    #[test]
    fn ansi256_fallback_uses_indexed_only() {
        let t = Theme::build("deep-tide", Depth::Ansi256).unwrap();
        assert!(matches!(t.text, Color::Indexed(_)) && matches!(t.accent, Color::Indexed(_)));
        assert_eq!(to_ansi256((0, 0, 0)), 16);
        assert_eq!(to_ansi256((255, 255, 255)), 231);
        assert!((232..=255).contains(&to_ansi256((128, 128, 128))));
    }

    #[test]
    fn every_status_has_icon_and_word() {
        let mut icons = std::collections::HashSet::new();
        for s in Status::ALL {
            assert!(!s.icon().is_empty() && !s.word().is_empty());
            assert!(s.label().contains(s.icon()) && s.label().contains(s.word()));
            icons.insert(s.icon());
        }
        assert_eq!(icons.len(), Status::ALL.len(), "icons must be distinct");
    }
}
