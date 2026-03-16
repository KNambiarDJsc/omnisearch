"""
parser.py — File content extraction and MIME routing.

This is the dedicated parsing layer between raw files and the embedding pipeline.

Pipeline:
  file path
    ↓
  detect_file_type()   → FileInfo (mime, category, is_text)
    ↓
  extract_content()    → ParsedContent (text or raw bytes + mime)
    ↓
  get_display_snippet() → str (for Qdrant payload)

Categories:
  document  → MarkItDown → text string
  code      → UTF-8 read → text string
  image     → raw bytes  → Gemini multimodal
  audio     → raw bytes  → Gemini multimodal
  video     → raw bytes  → Gemini multimodal
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# MIME + category registry
# ─────────────────────────────────────────────

@dataclass
class FileInfo:
    ext: str
    mime_type: str
    category: str          # document | code | image | audio | video
    is_text: bool          # True → embed as text; False → embed as raw bytes


# Full registry — extension → FileInfo
FILE_REGISTRY: dict[str, FileInfo] = {
    # Documents
    ".pdf":  FileInfo(".pdf",  "application/pdf",  "document", True),
    ".docx": FileInfo(".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", True),
    ".doc":  FileInfo(".doc",  "application/msword", "document", True),
    ".txt":  FileInfo(".txt",  "text/plain",        "document", True),
    ".md":   FileInfo(".md",   "text/markdown",     "document", True),
    ".html": FileInfo(".html", "text/html",         "document", True),
    ".csv":  FileInfo(".csv",  "text/csv",          "document", True),
    # Code
    ".py":   FileInfo(".py",   "text/x-python",     "code", True),
    ".js":   FileInfo(".js",   "text/javascript",   "code", True),
    ".ts":   FileInfo(".ts",   "text/typescript",   "code", True),
    ".jsx":  FileInfo(".jsx",  "text/jsx",          "code", True),
    ".tsx":  FileInfo(".tsx",  "text/tsx",          "code", True),
    ".json": FileInfo(".json", "application/json",  "code", True),
    ".yaml": FileInfo(".yaml", "application/yaml",  "code", True),
    ".yml":  FileInfo(".yml",  "application/yaml",  "code", True),
    ".rs":   FileInfo(".rs",   "text/x-rust",       "code", True),
    ".go":   FileInfo(".go",   "text/x-go",         "code", True),
    ".java": FileInfo(".java", "text/x-java",       "code", True),
    ".cpp":  FileInfo(".cpp",  "text/x-c++src",     "code", True),
    ".c":    FileInfo(".c",    "text/x-csrc",       "code", True),
    ".sh":   FileInfo(".sh",   "text/x-shellscript","code", True),
    # Images
    ".png":  FileInfo(".png",  "image/png",         "image", False),
    ".jpg":  FileInfo(".jpg",  "image/jpeg",        "image", False),
    ".jpeg": FileInfo(".jpeg", "image/jpeg",        "image", False),
    ".webp": FileInfo(".webp", "image/webp",        "image", False),
    ".gif":  FileInfo(".gif",  "image/gif",         "image", False),
    # Audio
    ".mp3":  FileInfo(".mp3",  "audio/mpeg",        "audio", False),
    ".wav":  FileInfo(".wav",  "audio/wav",         "audio", False),
    ".m4a":  FileInfo(".m4a",  "audio/mp4",         "audio", False),
    ".ogg":  FileInfo(".ogg",  "audio/ogg",         "audio", False),
    # Video
    ".mp4":  FileInfo(".mp4",  "video/mp4",         "video", False),
    ".mov":  FileInfo(".mov",  "video/quicktime",   "video", False),
    ".avi":  FileInfo(".avi",  "video/x-msvideo",   "video", False),
    ".mkv":  FileInfo(".mkv",  "video/x-matroska",  "video", False),
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(FILE_REGISTRY.keys())


@dataclass
class ParsedContent:
    """Result of parsing a file — either text or raw bytes."""
    file_info: FileInfo
    text: Optional[str]        # set if is_text=True
    raw_bytes: Optional[bytes] # set if is_text=False
    snippet: str               # short display text for search results


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def detect_file_type(file_path: str | Path) -> FileInfo:
    """
    Return FileInfo for a given path.
    Raises ValueError for unsupported extensions.
    """
    ext = Path(file_path).suffix.lower()
    info = FILE_REGISTRY.get(ext)
    if info is None:
        raise ValueError(
            f"Unsupported file extension '{ext}'. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    return info


def parse_file(file_path: str | Path) -> ParsedContent:
    """
    Full parsing pipeline for a file.

    Routes to the correct extractor based on category:
      document/code → text extraction (MarkItDown or UTF-8)
      image/audio/video → raw bytes

    Returns ParsedContent ready for the embedding layer.
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    file_info = detect_file_type(path)

    if file_info.is_text:
        text = _extract_text(path, file_info)
        snippet = _make_text_snippet(text, path.name)
        return ParsedContent(
            file_info=file_info,
            text=text,
            raw_bytes=None,
            snippet=snippet,
        )
    else:
        raw_bytes = _read_bytes(path)
        snippet = _make_binary_snippet(path, file_info)
        return ParsedContent(
            file_info=file_info,
            text=None,
            raw_bytes=raw_bytes,
            snippet=snippet,
        )


# ─────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────

def _extract_text(path: Path, file_info: FileInfo) -> str:
    """
    Extract text from a document or code file.

    Strategy:
      - Code / plain text → direct UTF-8 read (fast, no overhead)
      - PDF / DOCX / HTML → MarkItDown (handles rich formatting)
    """
    if file_info.category == "code":
        return _read_text_direct(path)

    # Plain text — also read directly
    if file_info.ext in {".txt", ".md", ".csv"}:
        return _read_text_direct(path)

    # Rich documents — try MarkItDown first, fallback to direct read
    return _extract_with_markitdown(path)


def _read_text_direct(path: Path) -> str:
    """Fast UTF-8 read with error replacement."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Direct read failed for {path.name}: {e}")
        raise


def _extract_with_markitdown(path: Path) -> str:
    """
    Use MarkItDown to convert rich document formats to clean markdown text.
    Supports: PDF, DOCX, DOC, HTML and more.
    Falls back to direct read if MarkItDown fails or isn't installed.
    """
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(path))
        text = result.text_content.strip()
        if text:
            return text
        # Empty result — fall back
        logger.warning(f"MarkItDown returned empty content for {path.name}, falling back")
        return _read_text_direct(path)
    except ImportError:
        logger.warning("markitdown not installed — pip install markitdown")
        return _read_text_direct(path)
    except Exception as e:
        logger.warning(f"MarkItDown failed for {path.name}: {e}, falling back to direct read")
        return _read_text_direct(path)


# ─────────────────────────────────────────────
# Binary reading
# ─────────────────────────────────────────────

def _read_bytes(path: Path) -> bytes:
    """Read raw bytes for multimodal embedding."""
    try:
        return path.read_bytes()
    except Exception as e:
        logger.error(f"Failed to read bytes from {path.name}: {e}")
        raise


# ─────────────────────────────────────────────
# Snippet generation
# ─────────────────────────────────────────────

def _make_text_snippet(text: str, filename: str, max_chars: int = 220) -> str:
    """
    Generate a short display snippet from extracted text.
    Collapses whitespace and truncates cleanly at word boundary.
    """
    if not text or not text.strip():
        return f"[{Path(filename).suffix.upper().lstrip('.')} file — no extractable text]"

    # Collapse all whitespace
    cleaned = " ".join(text.split())

    if len(cleaned) <= max_chars:
        return cleaned

    # Truncate at last word boundary before limit
    truncated = cleaned[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]

    return truncated + "…"


def _make_binary_snippet(path: Path, file_info: FileInfo) -> str:
    """Generate a snippet for binary files (image/audio/video)."""
    size_kb = path.stat().st_size // 1024
    size_str = f"{size_kb} KB" if size_kb < 1024 else f"{size_kb // 1024} MB"
    category_label = {
        "image": "Image",
        "audio": "Audio",
        "video": "Video",
    }.get(file_info.category, "Binary")
    return f"[{category_label} · {path.suffix.upper().lstrip('.')} · {size_str}]"


# ─────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────

def is_supported(file_path: str | Path) -> bool:
    """Quick check if a file extension is supported."""
    return Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS


def get_category_icon(category: str) -> str:
    """Return a display emoji for a file category."""
    return {
        "document": "📄",
        "code":     "💻",
        "image":    "🖼️",
        "audio":    "🎧",
        "video":    "🎬",
    }.get(category, "📁")