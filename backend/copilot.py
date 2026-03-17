import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Generator

logger = logging.getLogger(__name__)


# ── Response types ─────────────────────────────────────────────────

@dataclass
class CopilotSource:
    filename: str
    file_path: str
    file_type: str
    snippet: str
    relevance_score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CopilotResponse:
    answer: str
    sources: list[CopilotSource]
    query: str
    model: str
    elapsed_ms: int

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": [s.to_dict() for s in self.sources],
            "query": self.query,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
        }


# ── Context builder ────────────────────────────────────────────────

def _extract_full_text(file_path: str) -> Optional[str]:
    try:
        from parser import parse_file, FILE_REGISTRY
        path = Path(file_path)
        ext = path.suffix.lower()
        file_info = FILE_REGISTRY.get(ext)

        if file_info is None or not file_info.is_text:
            return None

        parsed = parse_file(file_path)
        return parsed.text
    except Exception as e:
        logger.warning(f"Could not extract text from {file_path}: {e}")
        return None


def _build_context(
    sources: list[CopilotSource],
    max_chars: int,
) -> str:
    sections = []
    total_chars = 0

    for i, source in enumerate(sources, 1):
        header = f"[Document {i}: {source.filename}]"

        # Try to get full text; fall back to snippet
        full_text = _extract_full_text(source.file_path)
        if full_text and full_text.strip():
            # Normalize whitespace
            body = " ".join(full_text.split())
        else:
            body = source.snippet or f"[{source.file_type.upper()} file — no text content]"

        section = f"{header}\n{body}"

        # Budget check — don't blow the context window
        if total_chars + len(section) > max_chars:
            # Trim this section to fit
            remaining = max_chars - total_chars - len(header) - 10
            if remaining > 100:
                section = f"{header}\n{body[:remaining]}…"
            else:
                break  # No space left

        sections.append(section)
        total_chars += len(section)

    return "\n\n---\n\n".join(sections)


_SYSTEM_PROMPT = """You are OmniSearch Copilot, an AI assistant that answers questions about the user's local files.

Rules:
- Answer based ONLY on the provided documents
- If the answer is not in the documents, say so clearly
- Cite which document(s) you used by referring to them as [Document N: filename]
- Be concise but complete
- For summaries, use bullet points
- For action items, use numbered lists
- Never hallucinate file contents not present in the context"""


# ── Main copilot function ──────────────────────────────────────────

def ask_copilot(
    query: str,
    top_k: Optional[int] = None,
) -> CopilotResponse:
    from config import settings
    from search import hybrid_search   # import here to avoid circular

    k = top_k or settings.copilot_context_docs
    t0 = time.time()

    # 1. Retrieve relevant documents via hybrid search
    logger.info(f"Copilot retrieving {k} docs for: '{query}'")
    search_results = hybrid_search(query, top_k=k)

    if not search_results:
        return CopilotResponse(
            answer="I couldn't find any relevant files in your index. Try indexing some folders first.",
            sources=[],
            query=query,
            model=settings.gemini_llm_model,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    # 2. Build source list
    sources = [
        CopilotSource(
            filename=r.filename,
            file_path=r.file_path,
            file_type=r.file_type,
            snippet=r.snippet,
            relevance_score=r.score,
        )
        for r in search_results
    ]

    # 3. Build context string
    context = _build_context(sources, max_chars=settings.copilot_max_context_chars)

    if not context.strip():
        return CopilotResponse(
            answer="I found some files but couldn't extract their text content for analysis.",
            sources=sources,
            query=query,
            model=settings.gemini_llm_model,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    # 4. Call Gemini LLM
    answer = _call_gemini(query, context, settings.gemini_llm_model)

    elapsed_ms = int((time.time() - t0) * 1000)
    logger.info(f"Copilot answered in {elapsed_ms}ms using {len(sources)} sources")

    return CopilotResponse(
        answer=answer,
        sources=sources,
        query=query,
        model=settings.gemini_llm_model,
        elapsed_ms=elapsed_ms,
    )


def _call_gemini(query: str, context: str, model: str) -> str:
    import os
    from google import genai
    from google.genai import types
    from config import settings

    api_key = settings.gemini_api_key
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    prompt = f"""Here are the relevant documents from the user's computer:

{context}

---

Question: {query}

Answer based on the documents above:"""

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.2,          # low temp for factual answers
                max_output_tokens=1024,
            ),
        )
        return response.text or "I couldn't generate an answer. Please try rephrasing your question."
    except Exception as e:
        logger.error(f"Gemini LLM call failed: {e}")
        raise


def stream_copilot(
    query: str,
    top_k: Optional[int] = None,
) -> Generator[dict, None, None]:
    from config import settings
    from search import hybrid_search

    k = top_k or settings.copilot_context_docs

    # 1. Retrieve
    search_results = hybrid_search(query, top_k=k)

    if not search_results:
        yield {"type": "chunk", "data": "No relevant files found. Try indexing more folders."}
        yield {"type": "done", "data": {"sources": []}}
        return

    sources = [
        CopilotSource(
            filename=r.filename,
            file_path=r.file_path,
            file_type=r.file_type,
            snippet=r.snippet,
            relevance_score=r.score,
        )
        for r in search_results
    ]

    # 2. Emit sources immediately so UI can show them while LLM streams
    yield {"type": "sources", "data": [s.to_dict() for s in sources]}

    # 3. Build context
    context = _build_context(sources, max_chars=settings.copilot_max_context_chars)

    # 4. Stream from Gemini
    import os
    from google import genai
    from google.genai import types

    api_key = settings.gemini_api_key
    client = genai.Client(api_key=api_key)

    prompt = f"""Here are the relevant documents from the user's computer:

{context}

---

Question: {query}

Answer based on the documents above:"""

    try:
        for chunk in client.models.generate_content_stream(
            model=settings.gemini_llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=1024,
            ),
        ):
            if chunk.text:
                yield {"type": "chunk", "data": chunk.text}
    except Exception as e:
        logger.error(f"Gemini streaming failed: {e}")
        yield {"type": "chunk", "data": f"\n\n[Error: {e}]"}

    yield {"type": "done", "data": {"sources": [s.to_dict() for s in sources]}}