-- 001_init.sql — Postgres projection schema v1 for TaskerKeeper VPS Hub.
-- Postgres is a read projection of the JSON source of truth; DAG logic stays in taskerkeeper/cli.py.
-- Projection only, never scheduling: all DAG transitions happen in cli.py under FileLock;
-- the projector mirrors JSON state here best-effort for fast multi-project reads.

CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_url TEXT,
    todo_path TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main'
);

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
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS machine_tokens (
    id TEXT PRIMARY KEY,
    prefix TEXT UNIQUE NOT NULL,
    hash TEXT NOT NULL,
    slugs TEXT[] NOT NULL DEFAULT '{}',
    scopes TEXT[] NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_events_slug_task ON events (slug, task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_snap_slug_status ON tasks_snap (slug, status);
CREATE INDEX IF NOT EXISTS idx_sessions_slug_seen ON sessions (slug, last_seen);
