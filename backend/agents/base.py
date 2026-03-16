"""
agents/base.py — Abstract base for all OmniSearch agents.

Every agent:
  - Has a name, description, and list of capabilities
  - Implements run(query, context) → AgentResult
  - Is stateless (state lives in LangGraph WorkflowState)
  - Can emit streaming events via a generator variant
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """
    Typed result from any agent.

    Fields:
      agent_name:    Which agent produced this result
      output:        The main text output (answer, summary, email, etc.)
      sources:       Files used to produce the output
      metadata:      Agent-specific extra data (word count, action items, etc.)
      success:       False if the agent encountered an unrecoverable error
      error:         Error message if success=False
      elapsed_ms:    Wall clock time
    """
    agent_name: str
    output: str
    sources: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class BaseAgent(ABC):
    """Abstract base class for all OmniSearch agents."""

    name: str = "base"
    description: str = "Base agent"
    capabilities: list[str] = []

    def __init__(self):
        self.logger = logging.getLogger(f"agent.{self.name}")

    @abstractmethod
    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        """
        Execute the agent.

        Args:
            query:    The user's request/question.
            context:  Shared context dict — may contain:
                        "documents"  → list of SearchResult dicts
                        "file_path"  → specific file to operate on
                        "history"    → prior workflow steps
                        "extra"      → any extra params from the orchestrator

        Returns:
            AgentResult
        """
        ...

    def _call_gemini(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """
        Shared Gemini LLM call — all agents use this.
        Handles client init, error logging, and return.
        """
        import os
        from google import genai
        from google.genai import types
        from config import settings

        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")

        client = genai.Client(api_key=api_key)

        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system:
            kwargs["system_instruction"] = system

        from google.genai import types as gtypes
        response = client.models.generate_content(
            model=settings.gemini_llm_model,
            contents=prompt,
            config=gtypes.GenerateContentConfig(**kwargs),
        )
        return response.text or ""

    def _get_doc_context(self, documents: list[dict], max_chars: int = 8000) -> str:
        """
        Build a numbered document context string from a list of SearchResult dicts.
        Tries to fetch full text; falls back to snippet.
        """
        sections = []
        total = 0

        for i, doc in enumerate(documents, 1):
            header = f"[Document {i}: {doc.get('filename', 'unknown')}]"
            full_text = self._try_full_text(doc.get("file_path", ""))
            body = full_text or doc.get("snippet", "[no content]")
            body = " ".join(body.split())

            section = f"{header}\n{body}"
            if total + len(section) > max_chars:
                remaining = max_chars - total - len(header) - 5
                if remaining > 80:
                    section = f"{header}\n{body[:remaining]}…"
                else:
                    break

            sections.append(section)
            total += len(section)

        return "\n\n---\n\n".join(sections)

    def _try_full_text(self, file_path: str) -> Optional[str]:
        """Try to extract full text from a file; return None on failure."""
        if not file_path:
            return None
        try:
            from parser import parse_file, FILE_REGISTRY
            from pathlib import Path
            ext = Path(file_path).suffix.lower()
            info = FILE_REGISTRY.get(ext)
            if info and info.is_text:
                parsed = parse_file(file_path)
                return parsed.text
        except Exception:
            pass
        return None

    def _timed_run(self, query: str, context: dict[str, Any]) -> AgentResult:
        """Wrapper around run() that measures elapsed time and catches errors."""
        t0 = time.time()
        try:
            result = self.run(query, context)
            result.elapsed_ms = int((time.time() - t0) * 1000)
            return result
        except Exception as e:
            self.logger.error(f"Agent {self.name} failed: {e}")
            return AgentResult(
                agent_name=self.name,
                output="",
                success=False,
                error=str(e),
                elapsed_ms=int((time.time() - t0) * 1000),
            )