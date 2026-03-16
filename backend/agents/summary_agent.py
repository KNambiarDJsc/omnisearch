"""
agents/summary_agent.py — Summarizes files or collections of documents.

Example queries:
  "Summarize research.pdf"
  "Give me a summary of all meeting notes"
  "What are the key points from these documents?"
  "TL;DR of the transformer paper"

Produces structured output:
  - Overview paragraph
  - Key points (bullets)
  - Action items (if any)
  - Word count metadata
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent, AgentResult


_SYSTEM = """You are a precise document summarizer.
Structure your summaries as:

**Overview**
[2-3 sentence overview]

**Key Points**
- [point 1]
- [point 2]
- [point 3]

**Action Items** (only if present)
1. [item]
2. [item]

Be concise. Prioritize facts over filler words."""


class SummaryAgent(BaseAgent):

    name = "summary"
    description = "Summarizes individual files or document collections"
    capabilities = [
        "summarize file",
        "give me a summary",
        "tldr",
        "key points",
        "main ideas",
        "what is this document about",
    ]

    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        documents = context.get("documents", [])

        specific_file = context.get("file_path")
        if specific_file:
            full_text = self._try_full_text(specific_file)
            if full_text:
                documents = [{
                    "filename": specific_file.split("/")[-1],
                    "file_path": specific_file,
                    "file_type": specific_file.split(".")[-1],
                    "snippet": full_text[:200],
                }]

        if not documents:
            from search import hybrid_search
            clean_query = (query
                           .lower()
                           .replace("summarize", "")
                           .replace("summary of", "")
                           .replace("tldr", "")
                           .strip())
            results = hybrid_search(clean_query or query, top_k=5)
            documents = [r.to_dict() for r in results]

        if not documents:
            return AgentResult(
                agent_name=self.name,
                output="No documents found to summarize. Try indexing some folders first.",
                sources=[],
            )

        is_single = len(documents) == 1
        doc_context = self._get_doc_context(documents, max_chars=10000)

        if is_single:
            fname = documents[0].get("filename", "document")
            prompt = f"""Summarize this document:

{doc_context}

Provide a structured summary of: {fname}"""
        else:
            prompt = f"""Summarize these {len(documents)} documents:

{doc_context}

User request: {query}

Provide a combined structured summary."""

        summary = self._call_gemini(prompt, system=_SYSTEM, temperature=0.2, max_tokens=1024)

        total_words = sum(
            len(d.get("snippet", "").split())
            for d in documents
        )

        return AgentResult(
            agent_name=self.name,
            output=summary,
            sources=documents[:8],
            metadata={
                "docs_summarized": len(documents),
                "approx_source_words": total_words,
                "single_doc": is_single,
            },
        )