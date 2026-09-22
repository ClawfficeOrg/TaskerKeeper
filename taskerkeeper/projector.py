"""taskerkeeper.projector — Postgres/sqlite read projection of the JSON source of truth.

Projection only, never scheduling: all DAG transitions happen in
taskerkeeper.cli under FileLock; this module mirrors JSON state into
``tasks_snap`` and ``events`` tables best-effort for fast multi-project reads
(task 1.2.1, after SQL migration 001_init.sql and locked POST writes 1.1.2).

Reads status/title/touches from the loaded todo only — no DAG logic lives
here. The projector runs as a separate process/loop tailing each slug's todo
file plus ``*.events.jsonl``; it is never called from the serve.py write path,
so a projection failure can never fail a write (same rule as the event log
and the sessions store: ``project_once`` swallows per-slug errors and reports
them in its return value).

Works against Postgres (psycopg, optional) or sqlite3 through the DBAPI
``conn`` the caller passes in. The ``psycopg`` import is guarded — when it is
absent everything still works against sqlite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # optional driver; sqlite fallback needs nothing
    import psycopg  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - driver presence varies
    psycopg = None  # type: ignore[assignment]

_SQLITE_TASKS_SNAP = """
CREATE TABLE IF NOT EXISTS tasks_snap (
    slug TEXT NOT NULL,
    task_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    agent_tier TEXT,
    provider TEXT,
    model TEXT,
    touches TEXT NOT NULL DEFAULT '[]',
    updated_seq INTEGER,
    PRIMARY KEY (slug, task_id)
);
"""

_SQLITE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    slug TEXT NOT NULL,
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    kind TEXT NOT NULL,
    actor TEXT,
    repo TEXT,
    branch TEXT,
    at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_slug_task ON events (slug, task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_snap_slug_status ON tasks_snap (slug, status);
"""

_PG_TASKS_SNAP = """
CREATE TABLE IF NOT EXISTS tasks_snap (
    slug TEXT NOT NULL,
    task_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    agent_tier TEXT,
    provider TEXT,
    model TEXT,
    touches JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_seq BIGINT,
    PRIMARY KEY (slug, task_id)
);
"""

_PG_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    slug TEXT NOT NULL,
    seq BIGSERIAL PRIMARY KEY,
    task_id TEXT,
    kind TEXT NOT NULL,
    actor TEXT,
    repo TEXT,
    branch TEXT,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_slug_task ON events (slug, task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_snap_slug_status ON tasks_snap (slug, status);
"""


def is_postgres(conn: Any) -> bool:
    """True when ``conn`` looks like a psycopg connection."""
    module = type(conn).__module__ or ""
    return "psycopg" in module or "pg8000" in module or "psycopg2" in module


def _adapt(sql: str, conn: Any) -> str:
    """Swap ``?`` placeholders for ``%s`` on Postgres drivers."""
    if is_postgres(conn):
        return sql.replace("?", "%s")
    return sql


def ensure_schema(conn: Any) -> None:
    """Create ``tasks_snap``/``events`` when missing. Idempotent."""
    statements = (_PG_TASKS_SNAP, _PG_EVENTS) if is_postgres(conn) else (_SQLITE_TASKS_SNAP, _SQLITE_EVENTS)
    cur = conn.cursor()
    try:
        for chunk in statements:
            for stmt in [s.strip() for s in chunk.strip().split(";") if s.strip()]:
                cur.execute(stmt)
    finally:
        cur.close()
    try:
        conn.commit()
    except Exception:
        pass


def iter_task_rows(todo_data: dict) -> Any:
    """Yield ``(phase_id, task)`` pairs in document order.

    Reads the loaded todo only; no prerequisite/ready evaluation here.
    """
    for phase in todo_data.get("phases", []) or []:
        pid = str(phase.get("id", ""))
        for task in phase.get("tasks", []) or []:
            yield pid, task


def _touches_json(task: dict) -> str:
    touches = task.get("touches") or []
    if isinstance(touches, str):
        touches = [touches]
    return json.dumps(list(touches), ensure_ascii=False)


_RESNAP_SQL = """
INSERT INTO tasks_snap
    (slug, task_id, phase_id, status, title, agent_tier, provider, model, touches, updated_seq)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(slug, task_id) DO UPDATE SET
    phase_id = excluded.phase_id,
    status = excluded.status,
    title = excluded.title,
    agent_tier = excluded.agent_tier,
    provider = excluded.provider,
    model = excluded.model,
    touches = excluded.touches,
    updated_seq = excluded.updated_seq
"""


def resnap_todo(conn: Any, slug: str, todo_data: dict, seq: int | None = None) -> int:
    """Upsert one row per task in ``todo_data``. Returns rows written.

    ``seq`` stamps ``updated_seq`` so readers can order snapshots; ``None``
    leaves the column NULL. Only ``status``/``title``/``touches`` (plus the
    ``agent``/``provider``/``model`` labels) are mirrored — scheduling stays
    in ``taskerkeeper.cli``.
    """
    ensure_schema(conn)
    rows = 0
    cur = conn.cursor()
    try:
        for phase_id, task in iter_task_rows(todo_data):
            task_id = str(task.get("id", ""))
            if not task_id:
                continue
            cur.execute(
                _adapt(_RESNAP_SQL, conn),
                (
                    slug,
                    task_id,
                    phase_id,
                    str(task.get("status", "")),
                    str(task.get("title", "")),
                    task.get("agent"),
                    task.get("provider"),
                    task.get("model"),
                    _touches_json(task),
                    seq,
                ),
            )
            rows += 1
    finally:
        cur.close()
    try:
        conn.commit()
    except Exception:
        pass
    return rows


def _norm_payload(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True)
        except (json.JSONDecodeError, ValueError):
            return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _event_key(event: dict) -> tuple[str, str, str, str]:
    kind = str(event.get("event") or event.get("kind") or "event")
    task_id = str(event.get("task") or event.get("task_id") or "")
    at = str(event.get("at") or "")
    return (at, kind, task_id, _norm_payload(event))


_INGEST_SQL = """
INSERT INTO events (slug, task_id, kind, actor, repo, branch, at, payload)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def ingest_events(conn: Any, slug: str, events: list[dict]) -> int:
    """Append ``events`` rows new to this slug. Returns rows inserted.

    Dedups on ``(at, kind, task_id, full-payload)`` so re-tailing the same
    ``*.events.jsonl`` never duplicates rows.
    """
    items = [e for e in events if isinstance(e, dict)]
    if not items:
        return 0
    ensure_schema(conn)
    cur = conn.cursor()
    try:
        cur.execute(
            _adapt("SELECT at, kind, task_id, payload FROM events WHERE slug = ?", conn),
            (slug,),
        )
        seen: set[tuple[str, str, str, str]] = set()
        for at_v, kind_v, task_v, payload_v in cur.fetchall():
            seen.add((str(at_v or ""), str(kind_v or ""), str(task_v or ""), _norm_payload(payload_v)))
        inserted = 0
        for event in items:
            key = _event_key(event)
            if key in seen:
                continue
            seen.add(key)
            at_v, kind_v, task_v, _ = key
            actor = event.get("owner") or event.get("actor")
            cur.execute(
                _adapt(_INGEST_SQL, conn),
                (
                    slug,
                    task_v or None,
                    kind_v,
                    str(actor) if actor is not None else None,
                    event.get("repo"),
                    event.get("branch"),
                    at_v or None,
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                ),
            )
            inserted += 1
    finally:
        cur.close()
    try:
        conn.commit()
    except Exception:
        pass
    return inserted


def _resolve_todo(registry_path: str | Path, todo_raw: str) -> str:
    """Resolve a registry ``todo_path`` the way serve.py does.

    Relative entries probe cwd, the registry's grandparent (deploy/ -> root),
    then the registry's own directory; first existing file wins.
    """
    if not todo_raw:
        return todo_raw
    if Path(todo_raw).is_absolute():
        return todo_raw
    registry = Path(registry_path)
    candidates = [
        Path.cwd() / todo_raw,
        registry.parent.parent / todo_raw,
        registry.parent / todo_raw,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(candidates[0])


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
    return out


def project_once(registry_path: str | Path, conn: Any, seq: int | None = None) -> dict:
    """Mirror every registry slug's todo + events.jsonl. Never raises.

    Returns ``{slug: {"tasks": n, "events": m}}`` per slug, or
    ``{slug: {"ok": False, "error": ...}}`` when that slug failed — a dead
    repo path must not stop the other slugs (best-effort, like the event log).
    """
    with open(registry_path, encoding="utf-8") as f:
        registry = json.load(f)
    report: dict = {}
    for entry in registry.get("projects", []) or []:
        slug = str(entry.get("slug", ""))
        if not slug:
            continue
        try:
            todo_path = _resolve_todo(str(registry_path), str(entry.get("todo_path", "")))
            with open(todo_path, encoding="utf-8") as f:
                todo_data = json.load(f)
            tasks = resnap_todo(conn, slug, todo_data, seq)
            events = ingest_events(conn, slug, _read_jsonl(Path(str(todo_path) + ".events.jsonl")))
            report[slug] = {"tasks": tasks, "events": events}
        except Exception as e:  # best-effort: report, never raise into the loop
            report[slug] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return report
