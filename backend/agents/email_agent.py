from __future__ import annotations

import re
from typing import Any

from agents.base import BaseAgent, AgentResult


_SYSTEM = """You are a professional email writer.
Write clear, concise, professional emails based on the provided documents.

Format your response EXACTLY as:
Subject: [subject line]

[email body with proper greeting and sign-off]

Guidelines:
- Match the tone to the context (formal for business, friendly for internal)
- Be concise — no padding
- Reference specific facts from the documents
- Include relevant action items or next steps
- End with a clear call to action if appropriate"""


class EmailAgent(BaseAgent):

    name = "email"
    description = "Generates professional emails from document context"
    capabilities = [
        "write email",
        "draft email",
        "compose email",
        "email from meeting",
        "email based on document",
        "follow up email",
    ]

    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        documents = context.get("documents", [])

        if not documents:
            from search import hybrid_search
            clean = re.sub(
                r"(write|draft|compose|create|generate)\s+(an?\s+)?email\s*(about|from|based on|summarizing)?",
                "", query, flags=re.IGNORECASE
            ).strip()
            results = hybrid_search(clean or query, top_k=4)
            documents = [r.to_dict() for r in results]

        if not documents:
            return AgentResult(
                agent_name=self.name,
                output="No relevant documents found to base the email on.",
                sources=[],
            )

        doc_context = self._get_doc_context(documents, max_chars=6000)

        recipient_hint = ""
        if "to " in query.lower():
            match = re.search(r"to ([a-zA-Z\s]+?)(?:\s+about|\s+from|\s+based|$)", query, re.IGNORECASE)
            if match:
                recipient_hint = f"Recipient: {match.group(1).strip()}\n"

        prompt = f"""Documents to base the email on:

{doc_context}

---

{recipient_hint}Email request: {query}

Write the email:"""

        email_text = self._call_gemini(prompt, system=_SYSTEM, temperature=0.4, max_tokens=800)

        subject = ""
        subject_match = re.match(r"Subject:\s*(.+)", email_text, re.IGNORECASE)
        if subject_match:
            subject = subject_match.group(1).strip()

        return AgentResult(
            agent_name=self.name,
            output=email_text,
            sources=documents[:4],
            metadata={
                "subject": subject,
                "docs_used": len(documents),
                "word_count": len(email_text.split()),
            },
        )