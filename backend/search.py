"""
search.py — Hybrid semantic + keyword search pipeline.

Combines Qdrant vector search with BM25 keyword search using
Reciprocal Rank Fusion (RRF) and BGE-Reranker cross-encoding.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Any

from storage import get_client
from bm25_index import get_bm25_index
from reranker import rerank, RerankCandidate
from config import settings

logger = logging.getLogger(__name__)

@dataclass
class SearchResult:
    """Unified search result object."""
    file_path: str
    filename: str
    file_type: str
    snippet: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

def semantic_search(query: str, top_k: Optional[int] = None) -> list[SearchResult]:
    """
    Pure vector search using Qdrant.
    Retrieves top_k nearest neighbors for the query embedding.
    """
    try:
        from embedder import embed_query
    except ImportError:
        logger.error("Embedder module not found — check imports")
        return []

    k = top_k or settings.top_k
    client = get_client()
    
    try:
        query_vector = embed_query(query)
    except Exception as e:
        logger.error(f"Failed to embed query: {e}")
        return []

    hits = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=query_vector,
        limit=k,
        with_payload=True,
    )

    return [
        SearchResult(
            file_path=h.payload.get("file_path", ""),
            filename=h.payload.get("filename", "unknown"),
            file_type=h.payload.get("file_type", "txt"),
            snippet=h.payload.get("snippet", ""),
            score=float(h.score),
            metadata=h.payload,
        )
        for h in hits
    ]

def hybrid_search(query: str, top_k: Optional[int] = None) -> list[SearchResult]:
    """
    Combined search pipeline:
      1. Retrieve BM25 candidates (keyword)
      2. Retrieve vector candidates (semantic)
      3. Merge using Reciprocal Rank Fusion (RRF)
      4. Rerank top 50 via Cross-Encoder (BAAI/bge-reranker-base)
    """
    k = top_k or settings.top_k
    
    # Pool size: larger than k to give reranker enough context
    pool_size = settings.rerank_pool if settings.reranker_enabled else k * 2
    
    # 1. Parallel retrieval (conceptual, sequential for simplicity)
    bm25_hits = get_bm25_index().search(query, top_k=pool_size)
    vector_hits = semantic_search(query, top_k=pool_size)
    
    # 2. Reciprocal Rank Fusion (RRF)
    # Allows merging scores from different metrics (0..1 cosine vs unscaled BM25)
    rrf_scores = {}  # file_path -> rrf_score
    doc_meta = {}    # file_path -> (filename, type, snippet)
    
    # BM25 RRF
    for rank, hit in enumerate(bm25_hits, 1):
        score = settings.bm25_weight / (rank + 60)
        rrf_scores[hit.file_path] = rrf_scores.get(hit.file_path, 0) + score
        doc_meta[hit.file_path] = (hit.filename, hit.file_type, hit.snippet)
        
    # Vector RRF
    for rank, hit in enumerate(vector_hits, 1):
        score = settings.vector_weight / (rank + 60)
        rrf_scores[hit.file_path] = rrf_scores.get(hit.file_path, 0) + score
        if hit.file_path not in doc_meta:
            doc_meta[hit.file_path] = (hit.filename, hit.file_type, hit.snippet)
            
    # Sort by merged RRF score
    sorted_paths = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 3. Reranking (optional but highly recommended)
    if settings.reranker_enabled and sorted_paths:
        candidates = [
            RerankCandidate(
                file_path=p,
                filename=doc_meta[p][0],
                file_type=doc_meta[p][1],
                snippet=doc_meta[p][2],
                hybrid_score=s
            )
            for p, s in sorted_paths[:settings.rerank_pool]
        ]
        
        try:
            results = rerank(query, candidates, top_k=k)
            return [
                SearchResult(
                    file_path=r.file_path,
                    filename=r.filename,
                    file_type=r.file_type,
                    snippet=r.snippet,
                    score=r.rerank_score,
                    metadata={"hybrid_score": r.hybrid_score}
                )
                for r in results
            ]
        except Exception as e:
            logger.warning(f"Reranking failed: {e} — falling back to RRF")
    
    # 4. Fallback to top_k from RRF
    return [
        SearchResult(
            file_path=p,
            filename=doc_meta[p][0],
            file_type=doc_meta[p][1],
            snippet=doc_meta[p][2],
            score=s,
            metadata={"source": "rrf"}
        )
        for p, s in sorted_paths[:k]
    ]
