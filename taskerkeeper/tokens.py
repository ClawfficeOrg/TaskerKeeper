"""taskerkeeper.tokens — per-project machine tokens for the VPS hub core API.

Mint/verify behind task 1.4.1. Stdlib + sqlite3 only. Secrets are never
stored: ``mint_token`` returns the presented token once, persists only its
SHA-256 hash, and ``verify_token`` re-hashes the presented secret with a
constant-time compare.

Schema mirrors the ``machine_tokens`` table in
deploy/migrations/001_init.sql (Postgres projection); this sqlite file is
the core's local copy. ``slugs``/``scopes`` are JSON arrays in sqlite,
TEXT[] in Postgres.

Token format: ``tk_<prefix>.<secret>`` where ``prefix`` is 8 hex chars
(lookup key, stored plaintext) and ``secret`` is 48 hex chars (hashed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

#: DDL for the local copy. IF NOT EXISTS so it can share a file with sessions.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_tokens (
    id TEXT PRIMARY KEY,
    prefix TEXT UNIQUE NOT NULL,
    hash TEXT NOT NULL,
    slugs TEXT NOT NULL DEFAULT '[]',
    scopes TEXT NOT NULL DEFAULT '[]',
    expires_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_machine_tokens_prefix ON machine_tokens (prefix);
"""

#: Serializes sqlite writes in-process; the file itself serializes the rest.
_LOCK = threading.Lock()


def utc_now() -> str:
    """Timestamp in the same ...Z form the todo files use."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: object) -> float | None:
    """An ...Z timestamp (or epoch number) as epoch seconds, None when absent."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _hash_secret(secret: str) -> str:
    """SHA-256 hex of the secret half. The secret itself is never stored."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _split_presented(presented: str) -> tuple[str, str] | None:
    """Split ``tk_<prefix>.<secret>`` into (prefix, secret). None when malformed."""
    raw = (presented or "").strip()
    if not raw or "." not in raw:
        return None
    head, _, secret = raw.rpartition(".")
    secret = secret.strip()
    prefix = head.strip()
    if prefix.startswith("tk_"):
        prefix = prefix[len("tk_"):]
    # Allow tk_<prefix> with extra underscores (e.g. future key ids): take tail.
    if "_" in prefix:
        prefix = prefix.rsplit("_", 1)[-1]
    if not prefix or not secret:
        return None
    return prefix, secret


def init_db(path: str | Path) -> str:
    """Create the machine_tokens table (parents included). Idempotent."""
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


def mint_token(
    db_path: str | Path,
    slugs: list[str],
    scopes: list[str],
    expires_days: float | None = 90,
) -> tuple[str, str, str]:
    """Mint one machine token. Returns (prefix, presented-once secret, stored hash).

    ``slugs`` limits which projects the token may touch (empty means none —
    pass explicit slugs); ``scopes`` limits what it may do (``"read"`` for
    GET, ``"write"`` for POST, ``"*"`` for everything). ``expires_days``
    counts from now; None means no expiry. Only the hash is stored — the
    presented token is shown once and cannot be recovered later.
    """
    clean_slugs = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    clean_scopes = [str(s).strip() for s in (scopes or []) if str(s).strip()]
    if not clean_slugs:
        raise ValueError("slugs is required (at least one project slug)")
    if not clean_scopes:
        raise ValueError("scopes is required (e.g. ['read'] or ['read', 'write'])")
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(24)
    presented = f"tk_{prefix}.{secret}"
    stored = _hash_secret(secret)
    token_id = secrets.token_hex(8)
    expires_at: str | None = None
    if expires_days is not None:
        expires_at = datetime.fromtimestamp(
            time.time() + float(expires_days) * 86400, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    init_db(db_path)
    with _LOCK:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute(
                "INSERT INTO machine_tokens"
                " (id, prefix, hash, slugs, scopes, expires_at, revoked, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    token_id,
                    prefix,
                    stored,
                    json.dumps(clean_slugs),
                    json.dumps(clean_scopes),
                    expires_at,
                    utc_now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return prefix, presented, stored


def verify_token(
    db_path: str | Path, presented: str
) -> tuple[list[str], list[str]] | None:
    """Check a presented token. Returns (slugs, scopes) or None.

    Refuses unknown prefixes, hash mismatches (constant-time), revoked rows,
    and expired rows. A missing db file verifies nothing (None) rather than
    raising, so a core without minted tokens still serves TK_API_TOKEN.
    """
    split = _split_presented(presented or "")
    if split is None:
        return None
    prefix, secret = split
    target = Path(db_path)
    if not target.is_file():
        return None
    conn = sqlite3.connect(str(target), timeout=10)
    try:
        row = conn.execute(
            "SELECT hash, slugs, scopes, expires_at, revoked"
            " FROM machine_tokens WHERE prefix = ?",
            (prefix,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    stored_hash, slugs_raw, scopes_raw, expires_at, revoked = row
    if revoked:
        return None
    if not hmac.compare_digest(str(stored_hash), _hash_secret(secret)):
        return None
    exp = _parse_ts(expires_at)
    if exp is not None and time.time() > exp:
        return None
    try:
        slugs = list(json.loads(slugs_raw or "[]"))
    except (json.JSONDecodeError, TypeError):
        slugs = []
    try:
        scopes = list(json.loads(scopes_raw or "[]"))
    except (json.JSONDecodeError, TypeError):
        scopes = []
    return [str(s) for s in slugs], [str(s) for s in scopes]


def authorize(
    db_path: str | Path,
    presented: str,
    slug: str | None = None,
    scope: str | None = None,
) -> tuple[list[str], list[str]] | None:
    """Verify plus slug/scope gate. Returns (slugs, scopes) or None.

    A token with an empty slugs list authorizes no project; pass ``slug``
    to require membership. Pass ``scope`` to require membership, with
    ``"*"`` acting as a wildcard for any scope.
    """
    result = verify_token(db_path, presented)
    if result is None:
        return None
    slugs, scopes = result
    if slug is not None and slug not in slugs:
        return None
    if scope is not None and scope not in scopes and "*" not in scopes:
        return None
    return slugs, scopes


def revoke_token(db_path: str | Path, prefix: str) -> bool:
    """Set the revoked flag for one prefix. True when a row was updated."""
    target = Path(db_path)
    if not target.is_file():
        return False
    clean = (prefix or "").strip()
    if clean.startswith("tk_"):
        clean = clean[len("tk_"):]
    if "_" in clean:
        clean = clean.rsplit("_", 1)[-1]
    if "." in clean:
        clean = clean.split(".", 1)[0]
    if not clean:
        return False
    with _LOCK:
        conn = sqlite3.connect(str(target), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            cursor = conn.execute(
                "UPDATE machine_tokens SET revoked = 1 WHERE prefix = ?",
                (clean,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def list_tokens(db_path: str | Path) -> list[dict]:
    """Metadata for every minted token (hashes included, secrets never stored)."""
    target = Path(db_path)
    if not target.is_file():
        return []
    conn = sqlite3.connect(str(target), timeout=10)
    try:
        rows = conn.execute(
            "SELECT id, prefix, slugs, scopes, expires_at, revoked, created_at"
            " FROM machine_tokens ORDER BY created_at ASC, prefix ASC"
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for token_id, prefix, slugs_raw, scopes_raw, expires_at, revoked, created in rows:
        try:
            slugs = list(json.loads(slugs_raw or "[]"))
        except (json.JSONDecodeError, TypeError):
            slugs = []
        try:
            scopes = list(json.loads(scopes_raw or "[]"))
        except (json.JSONDecodeError, TypeError):
            scopes = []
        out.append(
            {
                "id": token_id,
                "prefix": prefix,
                "slugs": slugs,
                "scopes": scopes,
                "expires_at": expires_at,
                "revoked": bool(revoked),
                "created_at": created,
            }
        )
    return out


def tokens_db_for_registry(registry: str, explicit: str | None = None) -> str:
    """Where machine tokens live: explicit flag, else $TK_TOKENS_DB, else beside the registry.

    Beside-the-registry keeps /srv layouts working with no extra flags, the
    same rule sessions.py uses for presence.
    """
    if explicit:
        return explicit
    env = os.environ.get("TK_TOKENS_DB", "").strip()
    if env:
        return env
    return str(Path(registry).parent / "tokens.db")


def mint(
    db_path: str | Path,
    slugs: list[str],
    scopes: list[str],
    expires_days: float | None = 90,
) -> str:
    """``tk mint`` helper: mint and return the presented token (print it once).

    The secret is shown only here — the db keeps the hash. Store it as a
    Bearer token; it cannot be recovered later, only revoked and re-minted.
    """
    _, presented, _ = mint_token(db_path, slugs, scopes, expires_days)
    return presented
