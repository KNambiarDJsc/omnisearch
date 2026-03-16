from __future__ import annotations

import logging
from typing import Any

from agents.base import BaseAgent, AgentResult

logger = logging.getLogger("agents.registry")


def _build_registry() -> dict[str, BaseAgent]:
    from agents.search_agent import SearchAgent
    from agents.qa_agent import QAAgent
    from agents.summary_agent import SummaryAgent
    from agents.email_agent import EmailAgent
    from agents.media_agent import MediaAgent

    agents = [SearchAgent(), QAAgent(), SummaryAgent(), EmailAgent(), MediaAgent()]
    return {a.name: a for a in agents}


_registry: dict[str, BaseAgent] | None = None


class AgentRegistry:
    """Static registry of all available agents."""

    @staticmethod
    def get(name: str) -> BaseAgent | None:
        global _registry
        if _registry is None:
            _registry = _build_registry()
        return _registry.get(name)

    @staticmethod
    def all() -> dict[str, BaseAgent]:
        global _registry
        if _registry is None:
            _registry = _build_registry()
        return dict(_registry)

    @staticmethod
    def names() -> list[str]:
        return list(AgentRegistry.all().keys())

    @staticmethod
    def descriptions() -> list[dict]:
        return [
            {
                "name": a.name,
                "description": a.description,
                "capabilities": a.capabilities,
            }
            for a in AgentRegistry.all().values()
        ]


def run_agent(
    agent_name: str,
    query: str,
    context: dict[str, Any] | None = None,
) -> AgentResult:
    """
    Run a specific agent by name.

    Args:
        agent_name:  One of: search, qa, summary, email, media
        query:       User's query/request
        context:     Optional context dict (documents, file_path, etc.)

    Returns:
        AgentResult
    """
    agent = AgentRegistry.get(agent_name)
    if agent is None:
        available = AgentRegistry.names()
        return AgentResult(
            agent_name=agent_name,
            output=f"Unknown agent '{agent_name}'. Available: {available}",
            success=False,
            error=f"Unknown agent: {agent_name}",
        )

    ctx = context or {}
    logger.info(f"Running agent '{agent_name}' for query: '{query[:60]}'")
    return agent._timed_run(query, ctx)