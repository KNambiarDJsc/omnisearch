import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RerankCandidate:
    """Input to the reranker."""
    file_path: str
    filename: str
    file_type: str
    snippet: str
    hybrid_score: float    # pre-rerank score from hybrid merge


@dataclass
class RerankResult:
    """Output from the reranker."""
    file_path: str
    filename: str
    file_type: str
    snippet: str
    rerank_score: float
    hybrid_score: float


# ── Lazy model cache ───────────────────────────────────────────────

_model = None
_model_name: Optional[str] = None


def _get_model(model_name: str):
    global _model, _model_name

    if _model is not None and _model_name == model_name:
        return _model

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        raise ImportError(
            "sentence-transformers not installed.\n"
            "Run: pip install sentence-transformers"
        )

    from config import settings
    logger.info(f"Loading reranker model: {model_name} (first call — may take 30s)")
    t0 = time.time()

    _model = CrossEncoder(model_name, device=settings.reranker_device)
    _model_name = model_name

    elapsed = time.time() - t0
    logger.info(f"Reranker loaded in {elapsed:.1f}s")
    return _model


# ── Public API ─────────────────────────────────────────────────────

def rerank(
    query: str,
    candidates: list[RerankCandidate],
    top_k: int = 5,
) -> list[RerankResult]:
    """
    Rerank candidates using the cross-encoder model.

    Args:
        query:      The user's search query.
        candidates: Up to 50 candidates from hybrid retrieval.
        top_k:      Number of results to return after reranking.

    Returns:
        top_k RerankResults sorted by rerank_score descending.
    """
    from config import settings

    if not candidates:
        return []

    if not settings.reranker_enabled:
        logger.debug("Reranker disabled — returning candidates by hybrid score")
        return _fallback(candidates, top_k)

    try:
        model = _get_model(settings.reranker_model)
    except ImportError as e:
        logger.warning(f"Reranker unavailable: {e} — falling back to hybrid scores")
        return _fallback(candidates, top_k)

    # Build (query, document_text) pairs for cross-encoder
    # Document text = filename + snippet (same text the user would read)
    pairs = [
        (query, _candidate_text(c))
        for c in candidates
    ]

    t0 = time.time()
    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.error(f"Cross-encoder prediction failed: {e} — falling back")
        return _fallback(candidates, top_k)

    elapsed = time.time() - t0
    logger.debug(f"Reranked {len(candidates)} candidates in {elapsed*1000:.0f}ms")

    # Sort by rerank score
    scored = sorted(
        zip(candidates, scores),
        key=lambda x: float(x[1]),
        reverse=True,
    )

    return [
        RerankResult(
            file_path=c.file_path,
            filename=c.filename,
            file_type=c.file_type,
            snippet=c.snippet,
            rerank_score=round(float(score), 4),
            hybrid_score=round(c.hybrid_score, 4),
        )
        for c, score in scored[:top_k]
    ]


def _candidate_text(c: RerankCandidate) -> str:
    """Build the document text for cross-encoder input."""
    return f"{c.filename}\n{c.snippet}"


def _fallback(candidates: list[RerankCandidate], top_k: int) -> list[RerankResult]:
    """Return top_k by hybrid score when reranker is unavailable."""
    sorted_cands = sorted(candidates, key=lambda c: c.hybrid_score, reverse=True)
    return [
        RerankResult(
            file_path=c.file_path,
            filename=c.filename,
            file_type=c.file_type,
            snippet=c.snippet,
            rerank_score=c.hybrid_score,
            hybrid_score=c.hybrid_score,
        )
        for c in sorted_cands[:top_k]
    ]


def is_reranker_loaded() -> bool:
    return _model is not None