use std::path::PathBuf;
use std::time::Duration;

use crossterm::event::{self, Event, KeyEventKind};
use tidetable_tui::app::{run_pending, App, Load, ModeArg};
use tidetable_tui::review::Ticks;
use tidetable_tui::source::{CliRunner, LocalSource, Source, SourceError};
use tidetable_tui::theme::Theme;
use tidetable_tui::ui;

const USAGE: &str = "usage: tidetable-tui [TODO_FILE] [--mode auto|standard|side|bottom] [--palette NAME] [--read-only] [--owner NAME]";

struct Args {
    file: Option<PathBuf>,
    mode: ModeArg,
    palette: Option<String>,
    read_only: bool,
    owner: Option<String>,
}

fn parse_args() -> Result<Args, String> {
    let mut a = Args { file: None, mode: ModeArg::Auto, palette: None, read_only: false, owner: None };
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--mode" => {
                let v = it.next().ok_or("--mode needs a value")?;
                a.mode = ModeArg::parse(&v).ok_or(format!("unknown mode {v:?}"))?;
            }
            "--palette" => a.palette = Some(it.next().ok_or("--palette needs a value")?),
            "--read-only" => a.read_only = true,
            "--owner" => a.owner = Some(it.next().ok_or("--owner needs a value")?),
            "-h" | "--help" => return Err(String::new()),
            f if !f.starts_with('-') => a.file = Some(PathBuf::from(f)),
            other => return Err(format!("unknown option {other}")),
        }
    }
    Ok(a)
}

fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64)
}

fn refresh(app: &mut App, source: &mut LocalSource<CliRunner>) {
    app.now_epoch = now_epoch();
    match source.snapshot() {
        Ok(s) => app.set_snapshot(s),
        Err(SourceError::Pick(files)) => app.load = Load::Pick(files),
        Err(e) => app.load = Load::Failed(e.to_string()),
    }
}

fn main() {
    let args = match parse_args() {
        Ok(a) => a,
        Err(m) => {
            if !m.is_empty() {
                eprintln!("{m}");
            }
            eprintln!("{USAGE}");
            std::process::exit(2);
        }
    };
    let (theme, warn) = Theme::select(args.palette.as_deref(), |k| std::env::var(k).ok());
    if let Some(w) = warn {
        eprintln!("{w}");
    }
    let cwd = std::env::current_dir().unwrap_or_default();
    let mut source = LocalSource::new(CliRunner::default(), cwd.clone()).with_owner(args.owner);
    if let Some(f) = args.file {
        source.choose(f);
    }
    let mut app = App::new(theme, args.mode, Ticks::load(&cwd));
    app.read_only = args.read_only;

    // ratatui::init installs a panic hook that restores the terminal.
    let mut terminal = ratatui::init();
    let _ = crossterm::execute!(std::io::stdout(), crossterm::event::EnableMouseCapture);
    refresh_first(&mut terminal, &mut app, &mut source);
    while !app.quit {
        if terminal.draw(|f| ui::draw(f, &mut app)).is_err() {
            break;
        }
        // Block on input; the timeout is the periodic refresh (3.2.2 makes this smarter).
        match event::poll(Duration::from_secs(5)) {
            Ok(true) => match event::read() {
                Ok(Event::Key(k)) if k.kind == KeyEventKind::Press => app.on_key(k),
                Ok(Event::Mouse(m)) => app.on_mouse(m),
                Ok(_) => {}
                Err(_) => break,
            },
            Ok(false) => app.want_refresh = true,
            Err(_) => break,
        }
        run_pending(&mut app, &mut source);
        if let Some(f) = app.chosen.take() {
            source.choose(f);
            app.want_refresh = true;
        }
        if app.want_refresh {
            app.want_refresh = false;
            refresh(&mut app, &mut source);
        }
    }
    let _ = crossterm::execute!(std::io::stdout(), crossterm::event::DisableMouseCapture);
    ratatui::restore();
}

/// Paint the Loading state once before the first (slow, Python-spawning) snapshot.
fn refresh_first(terminal: &mut ratatui::DefaultTerminal, app: &mut App, source: &mut LocalSource<CliRunner>) {
    let _ = terminal.draw(|f| ui::draw(f, app));
    refresh(app, source);
}
