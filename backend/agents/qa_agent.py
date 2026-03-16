"""
agents/qa_agent.py — Answers questions grounded in retrieved documents.

Example queries:
  "What did we decide in the budget meeting?"
  "What are the key findings in research.pdf?"
  "Who was responsible for the Q3 deliverables?"

Requires context["documents"] to be pre-populated (usually by SearchAgent).
Falls back to running its own search if documents are absent.
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent, AgentResult


_SYSTEM = """You are a precise question-answering assistant.
Answer ONLY based on the provided documents.
Cite sources as [Document N: filename].
If the answer is not in the documents, say so clearly — never hallucinate.
Be concise. Use bullet points for lists of facts."""


class QAAgent(BaseAgent):

    name = "qa"
    description = "Answers questions grounded in retrieved documents"
    capabilities = [
        "answer questions about files",
        "what did X say about Y",
        "find information in documents",
        "extract facts from files",
    ]

    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        documents = context.get("documents", [])

        if not documents:
            from search import hybrid_search
            results = hybrid_search(query, top_k=5)
            documents = [r.to_dict() for r in results]

        if not documents:
            return AgentResult(
                agent_name=self.name,
                output="No relevant documents found to answer your question.",
                sources=[],
            )

        doc_context = self._get_doc_context(documents)

        prompt = f"""Documents:

{doc_context}

---

Question: {query}

Answer based only on the documents above:"""

        answer = self._call_gemini(prompt, system=_SYSTEM, temperature=0.2)

        return AgentResult(
            agent_name=self.name,
            output=answer,
            sources=documents[:5],
            metadata={"docs_used": len(documents)},
        )