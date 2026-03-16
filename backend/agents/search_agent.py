"""
agents/search_agent.py — Retrieves relevant documents using hybrid search.

This is the entry point for all other agents — they typically call
SearchAgent first to populate context["documents"].

Also handles:
  - "find X" queries
  - "show me files about X"
  - "what files do I have about X"
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent, AgentResult


class SearchAgent(BaseAgent):

    name = "search"
    description = "Retrieves relevant files using hybrid BM25 + vector search"
    capabilities = [
        "find files",
        "search documents",
        "locate files by content",
        "show files about topic",
    ]

    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        from search import hybrid_search

        top_k = context.get("top_k", 8)
        self.logger.info(f"SearchAgent: '{query}' (top_k={top_k})")

        results = hybrid_search(query, top_k=top_k)

        if not results:
            return AgentResult(
                agent_name=self.name,
                output="No relevant files found. Try indexing more folders via Settings.",
                sources=[],
                metadata={"count": 0},
            )

        lines = [f"Found {len(results)} relevant file(s):\n"]
        for i, r in enumerate(results, 1):
            score_pct = int(r.score * 100)
            lines.append(f"{i}. **{r.filename}** ({r.file_type.upper()}, {score_pct}% match)")
            if r.snippet:
                lines.append(f"   {r.snippet[:120]}…")

        return AgentResult(
            agent_name=self.name,
            output="\n".join(lines),
            sources=[r.to_dict() for r in results],
            metadata={
                "count": len(results),
                "query": query,
            },
        )