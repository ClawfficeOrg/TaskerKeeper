"""taskerkeeper.sessions — sqlite-backed agent presence for the VPS hub core API.

Heartbeat store behind ``POST /api/<slug>/heartbeat`` (task 1.1.3). One row
per agent: the latest heartbeat plus which task it holds. Stale means the
agent has not been heard from in ``STALE_AFTER_SECONDS`` (180s) — the
dashboard greys those rows out.

Stdlib + sqlite3 only. Presence is best-effort by design (like the event
log): callers in serve.py swallow our errors so a sessions write can never
fail the todo write it rides along with.

Schema mirrors the ``sessions`` table in deploy/migrations/001_init.sql
(Postgres projection); this sqlite file is the core's local copy.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

#: Heartbeats older than this count as stale (dashboard greys them out).
STALE_AFTER_SECONDS = 180

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    agent_id TEXT PRIMARY KEY,
    slug TEXT,
    task_id TEXT,
    repo TEXT,
    branch TEXT,
    worktree TEXT,
    host TEXT,
    model TEXT,
    detail TEXT,
    last_seen TEXT NOT NULL,
    lease_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_slug_seen ON sessions (slug, last_seen);
"""

_COLUMNS = (
    "agent_id", "slug", "task_id", "repo", "branch", "worktree",
    "host", "model", "detail", "last_seen", "lease_expires_at",
)

#: Serializes sqlite writes in-process; the file itself serializes the rest.
_LOCK = threading.Lock()


def utc_now() -> str:
    """Timestamp in the same ...Z form the todo files use."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale(last_seen: object, now: datetime | None = None) -> bool:
    """True when a heartbeat timestamp is older than the stale threshold."""
    seen = _parse_ts(last_seen)
    if seen is None:
        return True
    return (now or datetime.now(timezone.utc)).timestamp() - seen.timestamp() > STALE_AFTER_SECONDS


def init_db(path: str | Path) -> str:
    """Create the sessions database (parents included). Idempotent."""
    target = Path(path)
    if target.parent != Path(""):
        target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = sqlite3.connect(str(target), timeout=10)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    return str(target)


def _row_to_dict(row: sqlite3.Row | tuple | None) -> dict | None:
    if row is None:
        return None
    values = dict(zip(_COLUMNS, tuple(row)))
    values["stale"] = is_stale(values.get("last_seen"))
    return values


def upsert_heartbeat(
    path: str | Path,
    agent_id: str,
    slug: str | None = None,
    task_id: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    worktree: str | None = None,
    host: str | None = None,
    model: str | None = None,
    detail: str | None = None,
    lease_expires_at: str | None = None,
) -> dict:
    """Record one heartbeat: insert the agent row or refresh it.

    ``None`` fields keep their previous value (partial heartbeats merge);
    only ``last_seen`` always refreshes. Use :func:`clear_task` to drop the
    held task — a heartbeat without a task does not clear it.
    """
    agent = str(agent_id or "").strip()
    if not agent:
        raise ValueError("agent_id is required")
    init_db(path)
    now = utc_now()
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute(
                """INSERT INTO sessions
                   (agent_id, slug, task_id, repo, branch, worktree,
                    host, model, detail, last_seen, lease_expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                     slug = COALESCE(excluded.slug, sessions.slug),
                     task_id = COALESCE(excluded.task_id, sessions.task_id),
                     repo = COALESCE(excluded.repo, sessions.repo),
                     branch = COALESCE(excluded.branch, sessions.branch),
                     worktree = COALESCE(excluded.worktree, sessions.worktree),
                     host = COALESCE(excluded.host, sessions.host),
                     model = COALESCE(excluded.model, sessions.model),
                     detail = COALESCE(excluded.detail, sessions.detail),
                     last_seen = excluded.last_seen,
                     lease_expires_at = COALESCE(excluded.lease_expires_at,
                                                 sessions.lease_expires_at)""",
                (agent, slug, task_id, repo, branch, worktree,
                 host, model, detail, now, lease_expires_at),
            )
            conn.commit()
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM sessions WHERE agent_id = ?",
                (agent,),
            ).fetchone()
        finally:
            conn.close()
    result = _row_to_dict(row)
    assert result is not None  # just inserted it
    return result


def clear_task(path: str | Path, agent_id: str) -> dict | None:
    """Drop the task an agent holds (done/reset path). Keeps the row.

    Returns the updated row, or None when the agent has never heartbeated.
    """
    agent = str(agent_id or "").strip()
    if not agent:
        return None
    init_db(path)
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            cursor = conn.execute(
                "UPDATE sessions SET task_id = NULL, last_seen = ? WHERE agent_id = ?",
                (utc_now(), agent),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM sessions WHERE agent_id = ?",
                (agent,),
            ).fetchone()
        finally:
            conn.close()
    return _row_to_dict(row)


def get_session(path: str | Path, agent_id: str) -> dict | None:
    """One agent row with ``stale`` computed, or None when unknown."""
    target = Path(path)
    if not target.is_file():
        return None
    conn = sqlite3.connect(str(target), timeout=10)
    try:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM sessions WHERE agent_id = ?",
            (str(agent_id),),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def list_sessions(path: str | Path, slug: str | None = None) -> list[dict]:
    """Every known agent, newest heartbeat first, each with ``stale`` computed."""
    target = Path(path)
    if not target.is_file():
        return []
    conn = sqlite3.connect(str(target), timeout=10)
    try:
        if slug is None:
            cursor = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM sessions "
                "ORDER BY last_seen DESC, agent_id ASC"
            )
        else:
            cursor = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM sessions WHERE slug = ? "
                "ORDER BY last_seen DESC, agent_id ASC",
                (slug,),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [row for row in (_row_to_dict(r) for r in rows) if row is not None]
