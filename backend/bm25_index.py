"""
bm25_index.py — BM25 keyword search index.

Why BM25 alongside vectors:
  - Vector search is great for semantics but misses exact matches
  - BM25 excels at: filenames, numbers, codes, IDs, camelCase identifiers
  - Hybrid = best of both worlds

Implementation:
  - rank-bm25 (BM25Okapi) for scoring
  - Persists to disk as a pickle (storage/bm25_index.pkl)
  - In-memory corpus rebuilt on load
  - Thread-safe via RWLock pattern (write lock on mutations)

Document representation:
  Each doc = tokenized(filename + " " + snippet + " " + filepath_components)
  We intentionally include the filename heavily — it's the most important signal.
"""

import logging
import pickle
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BM25Doc:
    """A document in the BM25 index."""
    file_path: str
    filename: str
    file_type: str
    snippet: str
    tokens: list[str]   # pre-tokenized for BM25


@dataclass
class BM25Result:
    file_path: str
    filename: str
    file_type: str
    snippet: str
    bm25_score: float


def _tokenize(text: str) -> list[str]:
    """
    Tokenize text for BM25.

    Strategy:
      - Lowercase
      - Split on whitespace and punctuation
      - Split camelCase and snake_case (important for code search)
      - Keep numbers intact
      - Remove single-char tokens
    """
    if not text:
        return []

    # Split camelCase: "myVariableName" → ["my", "Variable", "Name"]
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)

    # Split on non-alphanumeric (covers snake_case, kebab-case, dots, slashes)
    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())

    # Remove empties and single chars (except numbers)
    return [t for t in tokens if len(t) > 1]


def _doc_text(filename: str, snippet: str, file_path: str) -> str:
    """
    Build the text representation of a document for tokenization.
    Filename is repeated to boost its importance in BM25 scoring.
    """
    # Extract path components (folder names are useful context)
    path_parts = " ".join(Path(file_path).parts[-4:])  # last 4 path components
    stem = Path(filename).stem  # filename without extension

    # Repeat filename stem 3x to bias toward filename matches
    return f"{stem} {stem} {stem} {filename} {snippet} {path_parts}"


class BM25Index:
    """
    Thread-safe BM25 index with disk persistence.

    Usage:
        index = BM25Index(path)
        index.load()
        index.upsert(file_path, filename, file_type, snippet)
        results = index.search("invoice 2024", top_k=20)
        index.save()
    """

    def __init__(self, index_path: str):
        self._path = Path(index_path)
        self._docs: dict[str, BM25Doc] = {}   # file_path → BM25Doc
        self._bm25 = None                      # rank_bm25.BM25Okapi instance
        self._dirty = False                    # needs rebuild
        self._lock = threading.RLock()

    # ── Persistence ────────────────────────────────────────────────

    def load(self) -> None:
        """Load index from disk. Safe to call even if file doesn't exist."""
        if not self._path.exists():
            logger.info("BM25 index not found on disk — starting fresh")
            return

        try:
            with open(self._path, "rb") as f:
                data = pickle.load(f)
            self._docs = data.get("docs", {})
            logger.info(f"BM25 index loaded: {len(self._docs)} documents")
            self._rebuild()
        except Exception as e:
            logger.error(f"Failed to load BM25 index: {e} — starting fresh")
            self._docs = {}

    def save(self) -> None:
        """Persist index to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "wb") as f:
                pickle.dump({"docs": self._docs, "version": 1}, f, protocol=5)
            logger.debug(f"BM25 index saved ({len(self._docs)} docs)")
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    # ── Mutations ──────────────────────────────────────────────────

    def upsert(
        self,
        file_path: str,
        filename: str,
        file_type: str,
        snippet: str,
    ) -> None:
        """Add or update a document in the BM25 index."""
        text = _doc_text(filename, snippet, file_path)
        tokens = _tokenize(text)

        doc = BM25Doc(
            file_path=file_path,
            filename=filename,
            file_type=file_type,
            snippet=snippet,
            tokens=tokens,
        )

        with self._lock:
            self._docs[file_path] = doc
            self._dirty = True

        # Save async would be better in prod — for now save on every upsert
        self.save()

    def delete(self, file_path: str) -> bool:
        """Remove a document. Returns True if it existed."""
        with self._lock:
            if file_path not in self._docs:
                return False
            del self._docs[file_path]
            self._dirty = True

        self.save()
        return True

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 50) -> list[BM25Result]:
        """
        Score all documents against the query using BM25.
        Returns top_k results sorted by score descending.
        """
        with self._lock:
            if not self._docs:
                return []

            if self._dirty or self._bm25 is None:
                self._rebuild()

            query_tokens = _tokenize(query)
            if not query_tokens:
                return []

            try:
                scores = self._bm25.get_scores(query_tokens)
            except Exception as e:
                logger.error(f"BM25 scoring failed: {e}")
                return []

            doc_list = list(self._docs.values())
            scored = sorted(
                zip(doc_list, scores),
                key=lambda x: x[1],
                reverse=True,
            )

            results = []
            for doc, score in scored[:top_k]:
                if score > 0:
                    results.append(
                        BM25Result(
                            file_path=doc.file_path,
                            filename=doc.filename,
                            file_type=doc.file_type,
                            snippet=doc.snippet,
                            bm25_score=float(score),
                        )
                    )

            return results

    def doc_count(self) -> int:
        return len(self._docs)

    # ── Internal ───────────────────────────────────────────────────

    def _rebuild(self) -> None:
        """Rebuild BM25Okapi from current corpus. Must hold self._lock or be in init."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.error("rank-bm25 not installed — pip install rank-bm25")
            return

        if not self._docs:
            self._bm25 = None
            self._dirty = False
            return

        corpus = [doc.tokens for doc in self._docs.values()]
        self._bm25 = BM25Okapi(corpus)
        self._dirty = False
        logger.debug(f"BM25 index rebuilt: {len(corpus)} documents")


# ── Module-level singleton ─────────────────────────────────────────

_index: Optional[BM25Index] = None


def get_bm25_index() -> BM25Index:
    """Return the module-level BM25Index singleton. Loads from disk on first call."""
    global _index
    if _index is None:
        from config import settings
        _index = BM25Index(settings.bm25_index_path)
        _index.load()
    return _index