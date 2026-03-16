"""
cloud_sync.py — Cloudflare R2 backup and sync layer.

Architecture:
  LOCAL (always primary)         CLOUD (optional backup)
  ─────────────────────         ──────────────────────
  Qdrant vectors                R2: users/{id}/embeddings/snapshot_YYYYMMDD.json.enc
  SQLite metadata               R2: users/{id}/metadata/metadata_backup.json.enc
  BM25 index                    (not synced — rebuilt from snapshots)

Rules:
  - R2 is NEVER accessed during search queries
  - User files are NEVER uploaded (only embeddings + metadata)
  - All uploads are AES-256-GCM encrypted before leaving the machine
  - Encryption key = PBKDF2(user_id + machine_salt, iterations=200_000)
  - Push uses delta snapshots — only changed entries uploaded each sync

R2 bucket structure:
  omnisearch/
  └── users/
      └── {user_id}/
          ├── embeddings/
          │   ├── snapshot_20250316.json.enc   ← full snapshot
          │   └── delta_20250316_143022.json.enc
          ├── metadata/
          │   └── metadata_backup.json.enc
          ├── transcripts/
          │   └── meeting_transcripts/
          └── summaries/
              └── generated_reports/

Dependencies:
  pip install boto3 cryptography
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Encryption ────────────────────────────────────────────────────

def _derive_key(user_id: str, salt: str) -> bytes:
    """
    Derive a 32-byte AES key from user_id + machine salt using PBKDF2-HMAC-SHA256.
    200k iterations meets OWASP 2024 recommendation for PBKDF2-SHA256.
    """
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=(user_id + salt).encode(),
        iterations=200_000,
        backend=default_backend(),
    )
    return kdf.derive(b"omnisearch-v1")


def _get_machine_salt() -> str:
    """
    Stable per-machine salt derived from hostname + a stored UUID.
    Stored in config dir so it survives reboots.
    """
    from storage_manager import config_path
    salt_file = Path(config_path()).parent / "machine.salt"

    if salt_file.exists():
        return salt_file.read_text().strip()

    # Generate and persist
    import uuid
    salt = f"{socket.gethostname()}-{uuid.uuid4()}"
    salt_file.parent.mkdir(parents=True, exist_ok=True)
    salt_file.write_text(salt)
    return salt


def encrypt_payload(data: bytes, user_id: str) -> bytes:
    """
    AES-256-GCM encrypt data.
    Returns: 12-byte nonce + ciphertext + 16-byte tag (all concatenated).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_key(user_id, _get_machine_salt())
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)  # no AAD
    return nonce + ciphertext


def decrypt_payload(data: bytes, user_id: str) -> bytes:
    """Decrypt AES-256-GCM payload produced by encrypt_payload."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_key(user_id, _get_machine_salt())
    aesgcm = AESGCM(key)
    nonce = data[:12]
    ciphertext = data[12:]
    return aesgcm.decrypt(nonce, ciphertext, None)


# ── R2 client ─────────────────────────────────────────────────────

def _get_r2_client():
    """
    Build a boto3 S3 client pointed at Cloudflare R2.
    Reads credentials from environment / .env.
    """
    try:
        import boto3
    except ImportError:
        raise ImportError("boto3 not installed. Run: pip install boto3")

    from config import settings

    account_id = settings.r2_account_id
    access_key = settings.r2_access_key
    secret_key = settings.r2_secret_key

    if not all([account_id, access_key, secret_key]):
        raise ValueError(
            "R2 credentials not set. Add R2_ACCOUNT_ID, R2_ACCESS_KEY, "
            "R2_SECRET_KEY to backend/.env"
        )

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


# ── R2 object key helpers ─────────────────────────────────────────

def _user_prefix(user_id: str) -> str:
    return f"users/{user_id}"


def _embedding_key(user_id: str, filename: str) -> str:
    return f"{_user_prefix(user_id)}/embeddings/{filename}"


def _metadata_key(user_id: str) -> str:
    return f"{_user_prefix(user_id)}/metadata/metadata_backup.json.enc"


def _transcript_key(user_id: str, filename: str) -> str:
    return f"{_user_prefix(user_id)}/transcripts/meeting_transcripts/{filename}"


def _summary_key(user_id: str, filename: str) -> str:
    return f"{_user_prefix(user_id)}/summaries/generated_reports/{filename}"


# ── Core upload / download ────────────────────────────────────────

def _upload_encrypted(
    client,
    bucket: str,
    key: str,
    data: bytes,
    user_id: str,
    compress: bool = True,
) -> int:
    """
    Compress (optional) + encrypt + upload to R2.
    Returns size of uploaded bytes.
    """
    if compress:
        data = gzip.compress(data, compresslevel=6)

    encrypted = encrypt_payload(data, user_id)
    client.put_object(Bucket=bucket, Key=key, Body=encrypted)
    return len(encrypted)


def _download_decrypt(
    client,
    bucket: str,
    key: str,
    user_id: str,
    compressed: bool = True,
) -> bytes:
    """Download + decrypt + decompress from R2. Returns raw bytes."""
    response = client.get_object(Bucket=bucket, Key=key)
    encrypted = response["Body"].read()
    data = decrypt_payload(encrypted, user_id)
    if compressed:
        data = gzip.decompress(data)
    return data


# ── Public sync API ───────────────────────────────────────────────

def sync_embeddings_to_r2(
    user_id: str,
    delta_only: bool = True,
) -> dict:
    """
    Push embedding snapshot to R2.

    delta_only=True (default):
        Finds the latest local snapshot and uploads only the delta
        since the last R2 upload. Efficient for regular syncs.

    delta_only=False:
        Exports a full snapshot and uploads everything. Use for
        initial setup or after a full re-index.

    Returns sync result dict.
    """
    from config import settings
    from snapshot import export_snapshot, export_delta_snapshot, list_snapshots
    from storage_manager import log_sync, snapshots_path

    if not settings.enable_cloud_sync:
        return {"status": "skipped", "reason": "ENABLE_CLOUD_SYNC=false"}

    t0 = time.time()
    bucket = settings.r2_bucket_name

    try:
        client = _get_r2_client()

        # Decide what to upload
        if delta_only:
            snap_list = list_snapshots()
            if len(snap_list) >= 2:
                # Delta since the second-most-recent snapshot
                baseline = snap_list[1].path
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                snap_path = export_delta_snapshot(
                    since_snapshot_path=baseline,
                    label="r2sync",
                    compress=True,
                )
            else:
                # Not enough history — do a full export
                snap_path = export_snapshot(label="r2sync", compress=True)
        else:
            snap_path = export_snapshot(label="r2full", compress=True)

        # Read the snapshot
        snap_data = Path(snap_path).read_bytes()
        snap_filename = Path(snap_path).name

        # Parse to get count
        if snap_path.endswith(".gz"):
            count = json.loads(gzip.decompress(snap_data)).get("count", 0)
        else:
            count = json.loads(snap_data).get("count", 0)

        # Upload encrypted to R2
        key = _embedding_key(user_id, snap_filename + ".enc")
        size = _upload_encrypted(
            client, bucket, key, snap_data, user_id, compress=False
        )  # already compressed

        elapsed = int((time.time() - t0) * 1000)
        log_sync("push", count, "ok", provider="r2")

        logger.info(
            f"R2 sync push: {count} vectors → {key} "
            f"({size // 1024} KB, {elapsed}ms)"
        )
        return {
            "status":      "ok",
            "direction":   "push",
            "vectors":     count,
            "r2_key":      key,
            "size_kb":     size // 1024,
            "elapsed_ms":  elapsed,
        }

    except Exception as e:
        log_sync("push", 0, "error", error=str(e), provider="r2")
        logger.error(f"R2 sync push failed: {e}")
        raise


def sync_metadata_to_r2(user_id: str) -> dict:
    """
    Push SQLite metadata backup to R2 as encrypted JSON.
    Exports all file records from metadata.db → JSON → encrypt → upload.
    """
    from config import settings
    from storage_manager import get_all_indexed_files, log_sync

    if not settings.enable_cloud_sync:
        return {"status": "skipped", "reason": "ENABLE_CLOUD_SYNC=false"}

    t0 = time.time()
    bucket = settings.r2_bucket_name

    try:
        client = _get_r2_client()
        records = get_all_indexed_files()

        # Strip embedding vectors from metadata backup (they're in the snapshot)
        safe_records = [
            {k: v for k, v in r.items() if k != "embedding"}
            for r in records
        ]

        payload = json.dumps({
            "version":    "1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count":       len(safe_records),
            "records":     safe_records,
        }).encode()

        key = _metadata_key(user_id)
        size = _upload_encrypted(client, bucket, key, payload, user_id)

        elapsed = int((time.time() - t0) * 1000)
        log_sync("push_meta", len(safe_records), "ok", provider="r2")

        logger.info(f"R2 metadata push: {len(safe_records)} records → {key}")
        return {
            "status":     "ok",
            "records":    len(safe_records),
            "r2_key":     key,
            "size_kb":    size // 1024,
            "elapsed_ms": elapsed,
        }

    except Exception as e:
        log_sync("push_meta", 0, "error", error=str(e), provider="r2")
        logger.error(f"R2 metadata push failed: {e}")
        raise


def pull_embeddings_from_r2(
    user_id: str,
    snapshot_key: Optional[str] = None,
    merge: bool = True,
) -> dict:
    """
    Pull the latest (or specified) snapshot from R2 and import into Qdrant.

    snapshot_key: specific R2 object key. If None, uses the most recent.
    merge: passed to import_snapshot — True=upsert, False=wipe+replace.
    """
    from config import settings
    from snapshot import import_snapshot
    from storage_manager import snapshots_path, log_sync

    if not settings.enable_cloud_sync:
        return {"status": "skipped", "reason": "ENABLE_CLOUD_SYNC=false"}

    t0 = time.time()
    bucket = settings.r2_bucket_name

    try:
        client = _get_r2_client()
        prefix = _user_prefix(user_id) + "/embeddings/"

        # Find the key to download
        if not snapshot_key:
            snapshot_key = _latest_r2_key(client, bucket, prefix)
            if not snapshot_key:
                return {"status": "error", "reason": "No snapshots found in R2"}

        # Download + decrypt
        raw = _download_decrypt(client, bucket, snapshot_key, user_id, compressed=False)

        # Write to local snapshots dir for import
        local_filename = Path(snapshot_key).name.replace(".enc", "")
        local_path = Path(snapshots_path()) / f"r2pull_{local_filename}"
        local_path.write_bytes(raw)

        # Import into Qdrant
        result = import_snapshot(str(local_path), merge=merge)

        elapsed = int((time.time() - t0) * 1000)
        log_sync("pull", result["imported"], "ok", provider="r2")

        logger.info(f"R2 pull: {result['imported']} vectors restored from {snapshot_key}")
        return {
            "status":      "ok",
            "direction":   "pull",
            "r2_key":      snapshot_key,
            "imported":    result["imported"],
            "skipped":     result["skipped"],
            "elapsed_ms":  elapsed,
        }

    except Exception as e:
        log_sync("pull", 0, "error", error=str(e), provider="r2")
        logger.error(f"R2 pull failed: {e}")
        raise


def list_r2_snapshots(user_id: str) -> list[dict]:
    """List all snapshots stored in R2 for this user."""
    from config import settings
    bucket = settings.r2_bucket_name
    prefix = _user_prefix(user_id) + "/embeddings/"

    try:
        client = _get_r2_client()
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = response.get("Contents", [])
        return [
            {
                "key":          obj["Key"],
                "filename":     Path(obj["Key"]).name,
                "size_kb":      obj["Size"] // 1024,
                "last_modified": obj["LastModified"].isoformat(),
            }
            for obj in sorted(objects, key=lambda x: x["LastModified"], reverse=True)
        ]
    except Exception as e:
        logger.error(f"Failed to list R2 snapshots: {e}")
        return []


def get_sync_status(user_id: str) -> dict:
    """
    Return current sync status — last push/pull times + R2 snapshot list.
    Does NOT make an R2 call if cloud sync is disabled.
    """
    from config import settings
    from storage_manager import get_last_sync

    last = get_last_sync()
    result = {
        "cloud_sync_enabled": settings.enable_cloud_sync,
        "last_sync":          last,
        "r2_snapshots":       [],
    }

    if settings.enable_cloud_sync:
        try:
            result["r2_snapshots"] = list_r2_snapshots(user_id)
        except Exception as e:
            result["r2_error"] = str(e)

    return result


# ── Internal helpers ──────────────────────────────────────────────

def _latest_r2_key(client, bucket: str, prefix: str) -> Optional[str]:
    """Return the key of the most recently modified object under prefix."""
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    objects = response.get("Contents", [])
    if not objects:
        return None
    latest = max(objects, key=lambda x: x["LastModified"])
    return latest["Key"]