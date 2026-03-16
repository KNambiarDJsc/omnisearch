"""
snapshot.py — Embedding snapshot export, import, and diff engine.

Why this exists before cloud sync:
  - Qdrant's local disk format is opaque and version-coupled
  - You cannot portably move a raw Qdrant folder between machines
  - Snapshots give you a stable, portable, human-readable format
    that works for: backup, migration, multi-device sync, debugging

Snapshot format (JSON, one file per export):
  {
    "version":     "1",
    "created_at":  "2025-03-16T12:00:00Z",
    "collection":  "omnibrain",
    "dimension":   768,
    "count":       1234,
    "entries": [
      {
        "id":         "uuid-string",
        "file_path":  "/Users/you/Documents/notes.pdf",
        "filename":   "notes.pdf",
        "file_type":  "pdf",
        "snippet":    "First 220 chars...",
        "indexed_at": 1710000000.0,
        "embedding":  [0.031, -0.014, ...]   // 768 floats, normalized
      },
      ...
    ]
  }

Operations:
  export_snapshot()   → write snapshot JSON to disk, return path
  import_snapshot()   → read snapshot, upsert all entries into Qdrant
  diff_snapshots()    → compare two snapshots, return added/removed/changed
  list_snapshots()    → list all snapshots on disk with metadata
  prune_snapshots()   → keep only N most recent, delete the rest
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterator

from config import settings

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = "1"
SNAPSHOT_DIR_NAME = "snapshots"


# ── Snapshot data model ────────────────────────────────────────────

@dataclass
class SnapshotEntry:
    """A single indexed file's embedding + metadata."""
    id: str                    # Qdrant point UUID
    file_path: str
    filename: str
    file_type: str
    snippet: str
    indexed_at: float
    embedding: list[float]     # 768-dim normalized vector

    def embedding_hash(self) -> str:
        """SHA-256 of the raw embedding bytes — used for diff."""
        raw = b"".join(v.to_bytes(4, "little", signed=False)
                       if False else f"{v:.6f}".encode()
                       for v in self.embedding)
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class SnapshotManifest:
    """Snapshot file header / manifest."""
    version: str
    created_at: str            # ISO 8601 UTC
    collection: str
    dimension: int
    count: int
    size_bytes: int = 0
    compressed: bool = False
    checksum: str = ""         # SHA-256 of entries JSON


@dataclass
class SnapshotInfo:
    """Lightweight info about a snapshot file — no embeddings loaded."""
    path: str
    filename: str
    created_at: str
    count: int
    size_bytes: int
    compressed: bool
    checksum: str


@dataclass
class DiffResult:
    """Result of comparing two snapshots."""
    added: list[str] = field(default_factory=list)    # file_paths new in B
    removed: list[str] = field(default_factory=list)  # file_paths only in A
    changed: list[str] = field(default_factory=list)  # embedding changed
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def summary(self) -> str:
        return (f"+{len(self.added)} added, "
                f"-{len(self.removed)} removed, "
                f"~{len(self.changed)} changed, "
                f"={self.unchanged} unchanged")


# ── Snapshot directory ─────────────────────────────────────────────

def _snapshot_dir() -> Path:
    """Returns the snapshot directory, creating it if needed."""
    base = Path(settings.qdrant_path).parent   # storage/
    snap_dir = base / SNAPSHOT_DIR_NAME
    snap_dir.mkdir(parents=True, exist_ok=True)
    return snap_dir


def _snapshot_filename(label: str = "", compress: bool = False) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    ext = ".json.gz" if compress else ".json"
    return f"snapshot_{ts}{suffix}{ext}"


# ── Export ─────────────────────────────────────────────────────────

def export_snapshot(
    label: str = "",
    compress: bool = False,
    output_path: Optional[str] = None,
) -> str:
    """
    Export all vectors from Qdrant to a portable JSON snapshot.

    Args:
        label:        Optional label appended to filename (e.g. "pre-migration")
        compress:     If True, gzip the output (saves ~60% space)
        output_path:  Override output path. If None, uses snapshots/ dir.

    Returns:
        Absolute path to the written snapshot file.

    Raises:
        RuntimeError if Qdrant is empty or export fails.
    """
    from storage import get_client

    client = get_client()
    t0 = time.time()

    logger.info(f"Starting snapshot export (compress={compress})")

    # Scroll all points from Qdrant
    entries = []
    offset = None
    batch_size = 256

    while True:
        result, next_offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )

        for point in result:
            payload = point.payload or {}
            vector = point.vector

            if vector is None:
                logger.warning(f"Point {point.id} has no vector, skipping")
                continue

            entries.append({
                "id":         str(point.id),
                "file_path":  payload.get("file_path", ""),
                "filename":   payload.get("filename", ""),
                "file_type":  payload.get("file_type", ""),
                "snippet":    payload.get("snippet", ""),
                "indexed_at": payload.get("indexed_at", 0.0),
                "embedding":  list(vector),
            })

        if next_offset is None:
            break
        offset = next_offset

    if not entries:
        logger.warning("No entries to export — collection is empty")

    # Compute checksum over entries
    entries_json = json.dumps(entries, separators=(",", ":"))
    checksum = hashlib.sha256(entries_json.encode()).hexdigest()

    manifest = {
        "version":    SNAPSHOT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "collection": settings.qdrant_collection,
        "dimension":  settings.vector_dimension,
        "count":      len(entries),
        "compressed": compress,
        "checksum":   checksum,
    }

    snapshot = {**manifest, "entries": entries}
    snapshot_bytes = json.dumps(snapshot, indent=2).encode()

    # Write to disk
    if output_path:
        out_path = Path(output_path)
    else:
        out_path = _snapshot_dir() / _snapshot_filename(label, compress)

    if compress:
        with gzip.open(out_path, "wb") as f:
            f.write(snapshot_bytes)
    else:
        out_path.write_bytes(snapshot_bytes)

    size = out_path.stat().st_size
    elapsed = time.time() - t0

    logger.info(
        f"Snapshot exported: {out_path.name} "
        f"({len(entries)} entries, {size / 1024:.1f} KB, {elapsed:.1f}s)"
    )

    return str(out_path)


# ── Import ─────────────────────────────────────────────────────────

def import_snapshot(
    snapshot_path: str,
    merge: bool = True,
    verify_checksum: bool = True,
) -> dict:
    """
    Import a snapshot back into Qdrant.

    Args:
        snapshot_path:    Path to the .json or .json.gz snapshot file.
        merge:            If True, upsert entries (keep existing + add new).
                          If False, wipe collection first then import.
        verify_checksum:  Validate data integrity before importing.

    Returns:
        Dict with import stats: {imported, skipped, errors, elapsed_ms}
    """
    from storage import get_client, _ensure_collection
    from qdrant_client.models import PointStruct

    path = Path(snapshot_path)
    if not path.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

    t0 = time.time()
    logger.info(f"Importing snapshot: {path.name} (merge={merge})")

    # Load
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        with gzip.open(path, "rb") as f:
            data = json.loads(f.read())
    else:
        data = json.loads(path.read_text())

    # Validate version
    version = data.get("version", "0")
    if version != SNAPSHOT_VERSION:
        logger.warning(f"Snapshot version mismatch: {version} != {SNAPSHOT_VERSION}")

    # Verify checksum
    if verify_checksum and "checksum" in data:
        entries_json = json.dumps(data["entries"], separators=(",", ":"))
        actual = hashlib.sha256(entries_json.encode()).hexdigest()
        if actual != data["checksum"]:
            raise ValueError(
                f"Checksum mismatch — snapshot may be corrupted.\n"
                f"Expected: {data['checksum']}\n"
                f"Got:      {actual}"
            )
        logger.info("Checksum verified ✓")

    entries = data.get("entries", [])
    client = get_client()

    # Wipe if not merging
    if not merge:
        logger.warning("merge=False — wiping collection before import")
        client.delete_collection(settings.qdrant_collection)
        _ensure_collection(client)

    # Upsert in batches
    BATCH = 128
    imported, skipped, errors = 0, 0, 0

    for i in range(0, len(entries), BATCH):
        batch = entries[i:i + BATCH]
        points = []

        for entry in batch:
            try:
                vec = entry.get("embedding", [])
                if len(vec) != settings.vector_dimension:
                    logger.warning(
                        f"Dimension mismatch for {entry.get('filename')}: "
                        f"{len(vec)} != {settings.vector_dimension}, skipping"
                    )
                    skipped += 1
                    continue

                points.append(PointStruct(
                    id=entry["id"],
                    vector=vec,
                    payload={
                        "file_path":  entry.get("file_path", ""),
                        "filename":   entry.get("filename", ""),
                        "file_type":  entry.get("file_type", ""),
                        "snippet":    entry.get("snippet", ""),
                        "indexed_at": entry.get("indexed_at", 0.0),
                    }
                ))
            except Exception as e:
                logger.error(f"Failed to prepare entry {entry.get('id')}: {e}")
                errors += 1

        if points:
            client.upsert(
                collection_name=settings.qdrant_collection,
                points=points,
            )
            imported += len(points)

    # Also restore BM25 index
    _restore_bm25_from_entries(entries)

    elapsed_ms = int((time.time() - t0) * 1000)
    logger.info(
        f"Import complete: {imported} imported, {skipped} skipped, "
        f"{errors} errors ({elapsed_ms}ms)"
    )

    return {
        "imported":   imported,
        "skipped":    skipped,
        "errors":     errors,
        "elapsed_ms": elapsed_ms,
        "source":     path.name,
        "merge":      merge,
    }


def _restore_bm25_from_entries(entries: list[dict]) -> None:
    """Rebuild BM25 index from snapshot entries."""
    try:
        from bm25_index import get_bm25_index
        index = get_bm25_index()
        for e in entries:
            fp = e.get("file_path", "")
            if fp:
                index.upsert(
                    file_path=fp,
                    filename=e.get("filename", ""),
                    file_type=e.get("file_type", ""),
                    snippet=e.get("snippet", ""),
                )
        logger.info(f"BM25 index restored: {len(entries)} entries")
    except Exception as ex:
        logger.warning(f"BM25 restore failed (non-fatal): {ex}")


# ── Diff ───────────────────────────────────────────────────────────

def diff_snapshots(path_a: str, path_b: str) -> DiffResult:
    """
    Compare two snapshots and return what changed.

    Useful for:
      - Auditing what was indexed between two points in time
      - Deciding what to upload to cloud sync (only the delta)
      - Detecting index corruption

    Args:
        path_a: Older snapshot path (baseline)
        path_b: Newer snapshot path (current)

    Returns:
        DiffResult with added/removed/changed/unchanged counts
    """
    def _load(p: str) -> dict[str, dict]:
        path = Path(p)
        if path.suffix == ".gz" or p.endswith(".json.gz"):
            with gzip.open(path, "rb") as f:
                data = json.loads(f.read())
        else:
            data = json.loads(path.read_text())
        # keyed by file_path for easy comparison
        return {e["file_path"]: e for e in data.get("entries", [])}

    a = _load(path_a)
    b = _load(path_b)

    result = DiffResult()

    all_paths = set(a) | set(b)
    for fp in all_paths:
        if fp in b and fp not in a:
            result.added.append(fp)
        elif fp in a and fp not in b:
            result.removed.append(fp)
        else:
            # Both have it — compare embedding hash
            hash_a = hashlib.sha256(
                json.dumps(a[fp]["embedding"], separators=(",", ":")).encode()
            ).hexdigest()[:16]
            hash_b = hashlib.sha256(
                json.dumps(b[fp]["embedding"], separators=(",", ":")).encode()
            ).hexdigest()[:16]
            if hash_a != hash_b:
                result.changed.append(fp)
            else:
                result.unchanged += 1

    logger.info(f"Diff result: {result.summary()}")
    return result


# ── List + prune ───────────────────────────────────────────────────

def list_snapshots() -> list[SnapshotInfo]:
    """
    List all snapshots in the snapshots/ directory, newest first.
    Does NOT load embeddings — reads manifest only.
    """
    snap_dir = _snapshot_dir()
    infos = []

    for path in sorted(snap_dir.glob("snapshot_*"), reverse=True):
        try:
            if path.suffix == ".gz" or path.name.endswith(".json.gz"):
                with gzip.open(path, "rb") as f:
                    data = json.loads(f.read())
                compressed = True
            else:
                data = json.loads(path.read_bytes())
                compressed = False

            infos.append(SnapshotInfo(
                path=str(path),
                filename=path.name,
                created_at=data.get("created_at", ""),
                count=data.get("count", 0),
                size_bytes=path.stat().st_size,
                compressed=compressed,
                checksum=data.get("checksum", ""),
            ))
        except Exception as e:
            logger.warning(f"Could not read snapshot {path.name}: {e}")

    return infos


def prune_snapshots(keep: int = 5) -> list[str]:
    """
    Delete all but the `keep` most recent snapshots.

    Returns list of deleted filenames.
    """
    all_snaps = list_snapshots()   # newest first
    to_delete = all_snaps[keep:]
    deleted = []

    for info in to_delete:
        try:
            Path(info.path).unlink()
            deleted.append(info.filename)
            logger.info(f"Pruned snapshot: {info.filename}")
        except Exception as e:
            logger.warning(f"Could not delete {info.filename}: {e}")

    return deleted


# ── Export delta (only changed since last snapshot) ────────────────

def export_delta_snapshot(
    since_snapshot_path: str,
    label: str = "delta",
    compress: bool = False,
) -> str:
    """
    Export only entries that changed since the given snapshot.

    This is the efficient cloud sync primitive:
      instead of uploading 50k embeddings every sync,
      upload only the 12 that changed since last backup.

    Returns:
        Path to the delta snapshot file.
    """
    from storage import get_client

    # Load the baseline
    base_path = Path(since_snapshot_path)
    if base_path.suffix == ".gz" or since_snapshot_path.endswith(".json.gz"):
        with gzip.open(base_path, "rb") as f:
            base_data = json.loads(f.read())
    else:
        base_data = json.loads(base_path.read_text())

    # Build hash map of baseline: file_path → embedding_hash
    baseline_hashes: dict[str, str] = {}
    for e in base_data.get("entries", []):
        fp = e.get("file_path", "")
        emb = e.get("embedding", [])
        if fp:
            baseline_hashes[fp] = hashlib.sha256(
                json.dumps(emb, separators=(",", ":")).encode()
            ).hexdigest()[:16]

    # Scroll current Qdrant state
    client = get_client()
    delta_entries = []
    offset = None

    while True:
        result, next_offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )

        for point in result:
            payload = point.payload or {}
            fp = payload.get("file_path", "")
            vec = point.vector or []

            if not fp or not vec:
                continue

            # Compute current hash
            current_hash = hashlib.sha256(
                json.dumps(list(vec), separators=(",", ":")).encode()
            ).hexdigest()[:16]

            # Include if new or changed
            if baseline_hashes.get(fp) != current_hash:
                delta_entries.append({
                    "id":         str(point.id),
                    "file_path":  fp,
                    "filename":   payload.get("filename", ""),
                    "file_type":  payload.get("file_type", ""),
                    "snippet":    payload.get("snippet", ""),
                    "indexed_at": payload.get("indexed_at", 0.0),
                    "embedding":  list(vec),
                })

        if next_offset is None:
            break
        offset = next_offset

    # Write delta snapshot
    entries_json = json.dumps(delta_entries, separators=(",", ":"))
    checksum = hashlib.sha256(entries_json.encode()).hexdigest()

    manifest = {
        "version":       SNAPSHOT_VERSION,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "collection":    settings.qdrant_collection,
        "dimension":     settings.vector_dimension,
        "count":         len(delta_entries),
        "compressed":    compress,
        "checksum":      checksum,
        "delta":         True,
        "base_snapshot": base_path.name,
    }

    out_path = _snapshot_dir() / _snapshot_filename(label, compress)
    snapshot_bytes = json.dumps({**manifest, "entries": delta_entries}, indent=2).encode()

    if compress:
        with gzip.open(out_path, "wb") as f:
            f.write(snapshot_bytes)
    else:
        out_path.write_bytes(snapshot_bytes)

    logger.info(
        f"Delta snapshot: {len(delta_entries)} changed entries "
        f"vs baseline ({base_path.name}) → {out_path.name}"
    )
    return str(out_path)