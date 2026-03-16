"""
storage.py — Qdrant vector storage layer.

Local disk mode — no Docker required.
Collection: omnibrain | Distance: COSINE | Dimensions: 768
"""

import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[QdrantClient] = None


def get_client() -> QdrantClient:
    """Singleton Qdrant client — lazy init."""
    global _client
    if _client is None:
        storage_path = Path(settings.qdrant_path)
        storage_path.mkdir(parents=True, exist_ok=True)
        _client = QdrantClient(path=str(storage_path))
        _ensure_collection(_client)
        logger.info(f"Qdrant initialized at {storage_path}")
    return _client


def _ensure_collection(client: QdrantClient) -> None:
    """Create collection if it doesn't exist."""
    existing = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in existing:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(
                size=settings.vector_dimension,
                distance=Distance.COSINE,
            ),
        )
        logger.info(f"Created collection '{settings.qdrant_collection}' "
                    f"({settings.vector_dimension}d COSINE)")
    else:
        logger.info(f"Collection '{settings.qdrant_collection}' already exists")


def upsert_file(
    file_path: str,
    embedding: list[float],
    filename: str,
    file_type: str,
    snippet: str,
) -> str:
    """
    Upsert a file's embedding into Qdrant.
    Uses file_path as stable ID (hashed to int for qdrant).

    Returns the point ID.
    """
    client = get_client()

    # Deterministic ID from file path
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, file_path))

    payload = {
        "file_path": file_path,
        "filename": filename,
        "file_type": file_type,
        "snippet": snippet,
        "indexed_at": time.time(),
    }

    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            )
        ],
    )

    logger.debug(f"Upserted: {filename}")
    return point_id


def delete_file(file_path: str) -> None:
    """Remove a file's vector from Qdrant by file_path."""
    client = get_client()
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, file_path))
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=[point_id],
    )
    logger.debug(f"Deleted vector for: {file_path}")


def collection_stats() -> dict:
    """Return basic stats about the indexed collection."""
    client = get_client()
    info = client.get_collection(settings.qdrant_collection)
    return {
        "collection": settings.qdrant_collection,
        "vectors_count": info.vectors_count,
        "points_count": info.points_count,
        "dimension": settings.vector_dimension,
    }