"""
storage_manager.py — OS-aware storage paths + SQLite metadata database.

Responsibilities:
  1. Resolve the correct app-data directory per OS:
       macOS   → ~/Library/Application Support/OmniSearch/
       Windows → %LOCALAPPDATA%/OmniSearch/
       Linux   → ~/.local/share/OmniSearch/

  2. Bootstrap the full directory tree on first run.

  3. Own the SQLite metadata.db schema:
       files         — one row per indexed file (path, type, size, hash)
       indexed_files — per-file embedding hash + timestamp (for rebuild)

  4. Expose simple CRUD helpers used by brain.py / embedder.py.

Why SQLite alongside Qdrant?
  Qdrant stores vectors efficiently but its payload is not a relational DB.
  SQLite gives us:
    - Fast "have I already indexed this file at this mtime?" lookups
    - A complete file registry that lets us REBUILD Qdrant from scratch
      after a migration or corruption without re-running Gemini on everything
    - The embedding_hash lets us detect if a file changed and needs re-embedding
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── OS app-data root ──────────────────────────────────────────────

def get_app_data_root() -> Path:
    """
    Return the OS-appropriate application data directory.
    Creates it if it doesn't exist.

    macOS:   ~/Library/Application Support/OmniSearch
    Windows: %LOCALAPPDATA%/OmniSearch
    Linux:   ~/.local/share/OmniSearch
    """
    system = platform.system()

    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "OmniSearch"
    elif system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            base = Path(local_app) / "OmniSearch"
        else:
            base = Path.home() / "AppData" / "Local" / "OmniSearch"
    else:
        # Linux + anything else
        xdg = os.environ.get("XDG_DATA_HOME", "")
        if xdg:
            base = Path(xdg) / "OmniSearch"
        else:
            base = Path.home() / ".local" / "share" / "OmniSearch"

    base.mkdir(parents=True, exist_ok=True)
    return base


def bootstrap_storage() -> dict[str, Path]:
    """
    Create the full directory tree under the app-data root.
    Safe to call multiple times — all mkdir calls are idempotent.

    Returns a dict of named paths for easy reference.
    """
    root = get_app_data_root()

    dirs = {
        "root":      root,
        "qdrant":    root / "qdrant",
        "bm25":      root / "bm25",
        "metadata":  root / "metadata",
        "snapshots": root / "snapshots",
        "config":    root / "config",
        "logs":      root / "logs",
    }

    for name, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)

    # Migrate legacy storage/ directory if it exists next to the binary
    _maybe_migrate_legacy(root)

    logger.info(f"Storage root: {root}")
    return dirs


def _maybe_migrate_legacy(root: Path) -> None:
    """
    If the old storage/ directory exists in the repo, log a migration hint.
    We don't auto-migrate (destructive) but we tell the user.
    """
    # Find the repo root by walking up from this file
    repo_storage = Path(__file__).parent.parent / "storage" / "qdrant"
    if repo_storage.exists() and any(repo_storage.iterdir()):
        logger.warning(
            f"Legacy storage found at {repo_storage}. "
            f"Consider migrating to {root / 'qdrant'} by copying the directory, "
            f"or use /snapshot/export + /snapshot/import to migrate via snapshot."
        )


# ── Resolved paths (cached after first call) ─────────────────────

_paths: Optional[dict[str, Path]] = None


def paths() -> dict[str, Path]:
    global _paths
    if _paths is None:
        _paths = bootstrap_storage()
    return _paths


def qdrant_path() -> str:
    return str(paths()["qdrant"])


def bm25_path() -> str:
    return str(paths()["bm25"] / "bm25_index.pkl")


def metadata_db_path() -> str:
    return str(paths()["metadata"] / "metadata.db")


def snapshots_path() -> str:
    return str(paths()["snapshots"])


def watched_folders_path() -> str:
    return str(paths()["root"] / "watched_folders.json")


def config_path() -> str:
    return str(paths()["config"] / "settings.json")


# ── SQLite metadata database ──────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id             TEXT PRIMARY KEY,        -- uuid5 of file_path
    file_path      TEXT UNIQUE NOT NULL,
    filename       TEXT NOT NULL,
    file_type      TEXT NOT NULL,
    size_bytes     INTEGER DEFAULT 0,
    created_at     REAL NOT NULL,           -- unix timestamp
    updated_at     REAL NOT NULL,
    content_hash   TEXT NOT NULL DEFAULT '', -- SHA-256 of file content
    embedding_hash TEXT NOT NULL DEFAULT ''  -- SHA-256 of embedding vector
);

CREATE TABLE IF NOT EXISTS indexed_files (
    file_id        TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    qdrant_point   TEXT NOT NULL,           -- Qdrant point UUID
    indexed_at     REAL NOT NULL,           -- unix timestamp
    model          TEXT NOT NULL DEFAULT 'gemini-embedding-2-preview',
    dimension      INTEGER NOT NULL DEFAULT 768,
    PRIMARY KEY (file_id)
);

CREATE INDEX IF NOT EXISTS idx_files_path     ON files(file_path);
CREATE INDEX IF NOT EXISTS idx_files_updated  ON files(updated_at);
CREATE INDEX IF NOT EXISTS idx_indexed_point  ON indexed_files(qdrant_point);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at   REAL NOT NULL,
    direction   TEXT NOT NULL,  -- 'push' or 'pull'
    files_count INTEGER DEFAULT 0,
    status      TEXT NOT NULL,  -- 'ok' or 'error'
    error       TEXT DEFAULT '',
    provider    TEXT DEFAULT 'r2'
);
"""


@contextmanager
def get_db():
    """Context manager for SQLite connections. Auto-commits on exit."""
    db_file = metadata_db_path()
    conn = sqlite3.connect(db_file, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


# ── File metadata CRUD ────────────────────────────────────────────

def upsert_file_metadata(
    file_id: str,
    file_path: str,
    filename: str,
    file_type: str,
    size_bytes: int,
    content_hash: str,
    embedding_hash: str,
    qdrant_point: str,
    model: str = "gemini-embedding-2-preview",
    dimension: int = 768,
) -> None:
    """
    Upsert a file record into SQLite after indexing.
    Called from brain._index_single_file() after successful Qdrant upsert.
    """
    now = time.time()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO files
                (id, file_path, filename, file_type, size_bytes,
                 created_at, updated_at, content_hash, embedding_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                file_path      = excluded.file_path,
                filename       = excluded.filename,
                file_type      = excluded.file_type,
                size_bytes     = excluded.size_bytes,
                updated_at     = excluded.updated_at,
                content_hash   = excluded.content_hash,
                embedding_hash = excluded.embedding_hash
        """, (file_id, file_path, filename, file_type, size_bytes,
              now, now, content_hash, embedding_hash))

        conn.execute("""
            INSERT INTO indexed_files (file_id, qdrant_point, indexed_at, model, dimension)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                qdrant_point = excluded.qdrant_point,
                indexed_at   = excluded.indexed_at,
                model        = excluded.model,
                dimension    = excluded.dimension
        """, (file_id, qdrant_point, now, model, dimension))


def delete_file_metadata(file_id: str) -> None:
    """Remove a file's metadata record (cascades to indexed_files)."""
    with get_db() as conn:
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def get_file_metadata(file_path: str) -> Optional[dict]:
    """Fetch metadata for a file by path. Returns None if not found."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE file_path = ?", (file_path,)
        ).fetchone()
        return dict(row) if row else None


def file_needs_reindex(file_path: str) -> bool:
    """
    Return True if the file hasn't been indexed yet,
    or if its content has changed since last index.
    """
    path = Path(file_path)
    if not path.exists():
        return False

    meta = get_file_metadata(file_path)
    if meta is None:
        return True

    # Check content hash
    current_hash = _hash_file(path)
    return current_hash != meta.get("content_hash", "")


def get_all_indexed_files() -> list[dict]:
    """Return all indexed file records — used for snapshot + sync."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT f.*, i.qdrant_point, i.indexed_at, i.model, i.dimension
            FROM files f
            JOIN indexed_files i ON f.id = i.file_id
            ORDER BY f.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def metadata_stats() -> dict:
    """Return high-level stats about the metadata DB."""
    with get_db() as conn:
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        indexed_count = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
        last_indexed = conn.execute(
            "SELECT MAX(indexed_at) FROM indexed_files"
        ).fetchone()[0]
        return {
            "files":        file_count,
            "indexed":      indexed_count,
            "last_indexed": last_indexed,
            "db_path":      metadata_db_path(),
        }


def log_sync(
    direction: str,
    files_count: int,
    status: str,
    error: str = "",
    provider: str = "r2",
) -> None:
    """Record a sync event in the audit log."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sync_log (synced_at, direction, files_count, status, error, provider)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (time.time(), direction, files_count, status, error, provider))


def get_last_sync() -> Optional[dict]:
    """Return the most recent sync log entry."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT * FROM sync_log ORDER BY synced_at DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None


# ── Helpers ───────────────────────────────────────────────────────

def _hash_file(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 of file contents — used to detect changes."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def hash_embedding(embedding: list[float]) -> str:
    """Short SHA-256 of an embedding vector — used for change detection."""
    raw = ",".join(f"{v:.6f}" for v in embedding)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]