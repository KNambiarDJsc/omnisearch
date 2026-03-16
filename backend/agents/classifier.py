"""
agents/classifier.py — Intent classifier for query routing.

Routes user queries to the correct agent:
  "Summarize research.pdf"          → summary
  "Write email from meeting notes"  → email
  "What files do I have about ML?"  → search
  "What did we decide in Q3?"       → qa
  "Analyze the meeting recording"   → media
  "Prepare a report from my papers" → orchestrator (multi-step)

Strategy:
  1. Fast rule-based pre-classification (regex patterns, zero LLM cost)
  2. LLM fallback for ambiguous queries
  3. "orchestrator" intent triggers the LangGraph workflow (Phase 8)

Returns IntentResult with:
  - agent:      which agent to route to (or "orchestrator")
  - confidence: 0.0-1.0
  - reasoning:  why this routing was chosen
  - workflow:   for orchestrator intent, the suggested workflow steps
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("agents.classifier")


@dataclass
class IntentResult:
    agent: str                          # agent name or "orchestrator"
    confidence: float                   # 0.0 - 1.0
    reasoning: str
    workflow_hint: list[str] = field(default_factory=list)  # for orchestrator


# ── Rule-based patterns (fast path) ───────────────────────────────

_RULES: list[tuple[str, str, float]] = [
    # (regex pattern, agent, confidence)

    # Email
    (r"\b(write|draft|compose|create|generate)\s+(an?\s+)?(email|message|letter)\b", "email", 0.95),
    (r"\bemail\b.*(from|about|based on|summariz)", "email", 0.90),

    # Summary
    (r"\b(summarize|summarise|summary|tldr|tl;dr|overview|brief|synopsis)\b", "summary", 0.92),
    (r"\bkey (points|takeaways|ideas|findings)\b", "summary", 0.88),
    (r"\bwhat('?s| is| are) (this|the main|the key).*(about|in|from)\b", "summary", 0.82),

    # Media / Audio / Video
    (r"\b(transcribe|transcript)\b", "media", 0.95),
    (r"\b(meeting|call|recording|audio|video)\b.*(discuss|said|decided|action)", "media", 0.88),
    (r"\banalyze\b.*(audio|video|image|photo|recording|mp3|mp4|wav)\b", "media", 0.92),
    (r"\bwhat('?s| is).*(in|on).*(image|photo|picture|screenshot)\b", "media", 0.90),

    # QA
    (r"\b(what|who|when|where|why|how)\b.*(did|was|were|has|have|is|are)\b", "qa", 0.75),
    (r"\b(tell me|explain|describe).*(about|what|how|why)\b", "qa", 0.72),
    (r"\bfind (information|details|facts|answers?) (about|on|in)\b", "qa", 0.80),

    # Orchestrator (multi-step workflows)
    (r"\b(prepare|compile|create|build|generate)\s+a?\s*(report|analysis|review|digest)\b", "orchestrator", 0.88),
    (r"\b(from|across|over)\s+(all|my|the)\s+(files|documents|papers|notes)\b", "orchestrator", 0.82),
    (r"\b(research|analyze|process)\s+(all|multiple|several|my)\b", "orchestrator", 0.78),

    # Search (default for find/show/list)
    (r"\b(find|search|show|list|get|look for)\b.*(files?|documents?|papers?|notes?)\b", "search", 0.85),
    (r"\bdo i have\b.*(files?|documents?|anything)\b", "search", 0.88),
    (r"\bwhat files?\b", "search", 0.83),
]

_COMPILED_RULES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(pattern, re.IGNORECASE), agent, conf)
    for pattern, agent, conf in _RULES
]


def _rule_classify(query: str) -> Optional[IntentResult]:
    """Try rule-based classification. Returns None if no confident match."""
    best_agent = None
    best_conf = 0.0

    for pattern, agent, conf in _COMPILED_RULES:
        if pattern.search(query):
            if conf > best_conf:
                best_conf = conf
                best_agent = agent

    if best_agent and best_conf >= 0.75:
        return IntentResult(
            agent=best_agent,
            confidence=best_conf,
            reasoning=f"Rule-based match (pattern confidence: {best_conf:.0%})",
        )
    return None


# ── LLM-based classification (fallback) ───────────────────────────

_CLASSIFIER_SYSTEM = """You are an intent classifier for a file search assistant.

Given a user query, output ONLY a JSON object (no markdown, no explanation):
{
  "agent": "<agent_name>",
  "confidence": <0.0-1.0>,
  "reasoning": "<one sentence>",
  "workflow_hint": ["step1", "step2"]  // only for orchestrator agent
}

Available agents:
- "search":       Find files by topic or content
- "qa":           Answer questions about file contents
- "summary":      Summarize one or more files
- "email":        Write emails based on file content
- "media":        Analyze audio/video/image files
- "orchestrator": Multi-step workflows (report generation, research synthesis)

Use "orchestrator" when the query requires multiple steps across multiple files."""


def _llm_classify(query: str) -> IntentResult:
    """LLM-based classification for ambiguous queries."""
    import json
    import os
    from google import genai
    from google.genai import types
    from config import settings

    try:
        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("No API key")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",   # fast flash model for classification
            contents=f"Classify this query: {query}",
            config=types.GenerateContentConfig(
                system_instruction=_CLASSIFIER_SYSTEM,
                temperature=0.0,
                max_output_tokens=200,
            ),
        )

        text = response.text or ""
        # Strip markdown code fences if present
        text = re.sub(r"```(?:json)?|```", "", text).strip()
        data = json.loads(text)

        return IntentResult(
            agent=data.get("agent", "search"),
            confidence=float(data.get("confidence", 0.6)),
            reasoning=data.get("reasoning", "LLM classification"),
            workflow_hint=data.get("workflow_hint", []),
        )

    except Exception as e:
        logger.warning(f"LLM classification failed: {e} — defaulting to 'qa'")
        return IntentResult(
            agent="qa",
            confidence=0.5,
            reasoning=f"Fallback to qa after classifier error: {e}",
        )


# ── Public API ─────────────────────────────────────────────────────

class IntentClassifier:
    """Stateless intent classifier. Fast rule-based → LLM fallback."""

    def classify(self, query: str) -> IntentResult:
        return classify_intent(query)


def classify_intent(query: str) -> IntentResult:
    """
    Classify a user query and return the appropriate agent routing.

    Fast path: rule-based (0ms)
    Slow path: Gemini flash (~200ms) for ambiguous queries
    """
    if not query or not query.strip():
        return IntentResult(agent="search", confidence=1.0, reasoning="Empty query defaults to search")

    # Fast rule-based classification
    result = _rule_classify(query)
    if result:
        logger.debug(f"Rule classified '{query[:40]}' → {result.agent} ({result.confidence:.0%})")
        return result

    # LLM fallback for ambiguous queries
    logger.debug(f"Rule miss for '{query[:40]}' — falling back to LLM classifier")
    result = _llm_classify(query)
    logger.debug(f"LLM classified '{query[:40]}' → {result.agent} ({result.confidence:.0%})")
    return result