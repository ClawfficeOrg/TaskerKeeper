use tidetable_tui::{CliRunner, LocalSource, Source};

fn main() {
    // Chrome arrives in 3.1.x; for now print a one-line snapshot so the data layer is runnable.
    let cwd = std::env::current_dir().unwrap_or_default();
    let mut source = LocalSource::new(CliRunner::default(), cwd);
    match source.snapshot() {
        Ok(s) => println!(
            "{}: {} running, {} ready, {} blocked, {} done, {} to look at",
            s.project,
            s.in_progress.len(),
            s.ready.len(),
            s.blocked.len(),
            s.done.len(),
            s.look_queue.len()
        ),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    }
}
