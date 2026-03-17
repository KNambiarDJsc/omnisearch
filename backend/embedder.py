import logging
from pathlib import Path
from typing import Union

import numpy as np
from google import genai
from google.genai import types

from config import settings
from parser import parse_file, is_supported, ParsedContent, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

# Re-export for brain.py compatibility
WATCHED_EXTENSIONS = SUPPORTED_EXTENSIONS


def _get_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not loaded in settings")
    return genai.Client(api_key=settings.gemini_api_key)


def _normalize(values: list[float]) -> list[float]:
    arr = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        logger.warning("Zero-norm embedding — returning as-is")
        return values
    return (arr / norm).tolist()


def embed_query(query: str) -> list[float]:
    client = _get_client()
    result = client.models.embed_content(
        model="gemini-embedding-2-preview",
        contents=query,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=settings.vector_dimension,
        ),
    )
    return _normalize(result.embeddings[0].values)


def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed a text string."""
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    max_chars = 8192 * 5
    if len(text) > max_chars:
        logger.warning(f"Text truncated from {len(text)} to {max_chars} chars")
        text = text[:max_chars]
    client = _get_client()
    result = client.models.embed_content(
        model="gemini-embedding-2-preview",
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.vector_dimension,
        ),
    )
    return _normalize(result.embeddings[0].values)


def embed_bytes(raw_bytes: bytes, mime_type: str) -> list[float]:
    """Embed raw binary content (image, audio, video)."""
    client = _get_client()
    result = client.models.embed_content(
        model="gemini-embedding-2-preview",
        contents=[types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=settings.vector_dimension,
        ),
    )
    return _normalize(result.embeddings[0].values)


def embed_parsed(parsed: ParsedContent) -> list[float]:
    """
    Embed a ParsedContent object from parser.py.
    Main entry point for the indexing pipeline.
    """
    if parsed.file_info.is_text:
        if not parsed.text or not parsed.text.strip():
            raise ValueError("No extractable text content in file")
        return embed_text(parsed.text, task_type="RETRIEVAL_DOCUMENT")
    else:
        if not parsed.raw_bytes:
            raise ValueError("No raw bytes in parsed content")
        return embed_bytes(parsed.raw_bytes, parsed.file_info.mime_type)


def embed_file(file_path: Union[str, Path]) -> list[float]:
    """Full pipeline: file path → parse → embed → normalized vector."""
    parsed = parse_file(file_path)
    return embed_parsed(parsed)


def get_snippet(file_path: Union[str, Path], max_chars: int = 220) -> str:
    """Get display snippet — delegates to parser."""
    parsed = parse_file(file_path)
    return parsed.snippet