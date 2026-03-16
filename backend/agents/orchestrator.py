"""
agents/orchestrator.py — LangGraph multi-step workflow engine (Phase 8).

Handles complex multi-step queries like:
  "Prepare a report from all my research papers"
  "Analyze all meeting recordings and extract action items"
  "Summarize everything I have about machine learning"

LangGraph StateGraph:
  ┌─────────────────────────────────────────────────────┐
  │  WorkflowState (TypedDict)                          │
  │    query, documents, step_results, final_output ... │
  └─────────────────────────────────────────────────────┘
       ↓
  [plan] → [retrieve] → [process_docs] → [compile] → END

Nodes:
  plan_node        — LLM plans which workflow steps to run
  retrieve_node    — SearchAgent fetches relevant documents
  process_node     — Runs per-document agent (summary/qa/media)
  compile_node     — Synthesizes all step results into final output

Streaming:
  The orchestrator emits progress events via a queue so the
  FastAPI SSE endpoint can stream them to the frontend in real time.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Generator, Optional, TypedDict, Annotated
import operator

logger = logging.getLogger("agents.orchestrator")



class WorkflowState(TypedDict):
    """Shared state passed between all LangGraph nodes."""
    query: str
    plan: list[str]
    documents: list[dict]
    step_results: Annotated[list[dict], operator.add]
    final_output: str
    sources: list[dict]
    error: Optional[str]
    metadata: dict[str, Any]


@dataclass
class OrchestratorResult:
    """Result of a full multi-step workflow."""
    output: str
    sources: list[dict]
    steps: list[dict]
    query: str
    elapsed_ms: int
    plan: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)



@dataclass
class WorkflowEvent:
    type: str
    data: Any

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': self.type, 'data': self.data})}\n\n"



def plan_node(state: WorkflowState) -> dict:
    """
    Plan the workflow steps for this query.
    Uses Gemini to determine which agent sequence is needed.
    """
    query = state["query"]
    logger.info(f"Planning workflow for: '{query}'")

    plan = _plan_workflow(query)
    logger.info(f"Workflow plan: {plan}")

    return {
        "plan": plan,
        "step_results": [{"step": "plan", "output": f"Planned steps: {', '.join(plan)}"}],
        "metadata": {"plan_steps": plan},
    }


def retrieve_node(state: WorkflowState) -> dict:
    """Retrieve relevant documents using hybrid search."""
    from search import hybrid_search

    query = state["query"]
    logger.info(f"Retrieving documents for: '{query}'")

    results = hybrid_search(query, top_k=8)
    documents = [r.to_dict() for r in results]

    logger.info(f"Retrieved {len(documents)} documents")

    return {
        "documents": documents,
        "step_results": [{
            "step": "retrieve",
            "output": f"Retrieved {len(documents)} relevant files",
            "count": len(documents),
            "files": [d["filename"] for d in documents[:5]],
        }],
    }


def process_node(state: WorkflowState) -> dict:
    """
    Process documents through the appropriate agent(s) based on the plan.
    This is the main work node — runs summarization, QA, media analysis, etc.
    """
    from agents.registry import run_agent

    plan = state.get("plan", ["summary"])
    documents = state.get("documents", [])
    query = state["query"]

    if not documents:
        return {
            "step_results": [{"step": "process", "output": "No documents to process", "error": True}],
        }

    process_agent = "summary"
    for step in plan:
        if step in ("summary", "qa", "email", "media"):
            process_agent = step
            break

    logger.info(f"Processing {len(documents)} docs with agent '{process_agent}'")

    BATCH_SIZE = 5
    batches = [documents[i:i+BATCH_SIZE] for i in range(0, len(documents), BATCH_SIZE)]

    batch_results = []
    for i, batch in enumerate(batches):
        logger.debug(f"Processing batch {i+1}/{len(batches)} ({len(batch)} docs)")
        result = run_agent(
            process_agent,
            query=query,
            context={"documents": batch, "top_k": len(batch)},
        )
        batch_results.append({
            "batch": i + 1,
            "agent": process_agent,
            "output": result.output,
            "sources": result.sources,
            "success": result.success,
        })

    combined_output = "\n\n---\n\n".join(
        f"**Batch {r['batch']}:**\n{r['output']}"
        for r in batch_results
        if r["success"]
    )

    return {
        "step_results": [{
            "step": "process",
            "agent": process_agent,
            "output": combined_output,
            "batches": len(batches),
            "docs_processed": len(documents),
        }],
    }


def compile_node(state: WorkflowState) -> dict:
    """
    Compile all step results into a coherent final output.
    This is the synthesis step — creates a polished report/answer.
    """
    query = state["query"]
    step_results = state.get("step_results", [])
    documents = state.get("documents", [])
    plan = state.get("plan", [])

    logger.info("Compiling final output")

    process_outputs = [
        s["output"] for s in step_results
        if s.get("step") == "process" and s.get("output")
    ]

    if not process_outputs:
        return {
            "final_output": "No content was processed.",
            "sources": [],
        }

    combined = "\n\n".join(process_outputs)

    if "email" in plan:
        final_prompt = f"User request: {query}\n\nContent:\n{combined}\n\nProduce the final email:"
        system = "You are a professional email writer. Finalize this email draft."
    elif "report" in " ".join(plan).lower() or len(documents) > 3:
        final_prompt = _report_prompt(query, combined, documents)
        system = _REPORT_SYSTEM
    else:
        final_prompt = f"User request: {query}\n\nContent from files:\n{combined}\n\nSynthesize into a clear final answer:"
        system = "Synthesize the provided content into a clear, well-structured response."

    final_output = _call_gemini_compile(final_prompt, system)

    return {
        "final_output": final_output,
        "sources": documents[:8],
    }



def _build_graph():
    """Build and compile the LangGraph StateGraph."""
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        raise ImportError(
            "langgraph not installed.\nRun: pip install langgraph"
        )

    graph = StateGraph(WorkflowState)

    graph.add_node("plan", plan_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("process", process_node)
    graph.add_node("compile", compile_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "process")
    graph.add_edge("process", "compile")
    graph.add_edge("compile", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    """Lazy compile the graph — only built on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph



def run_workflow(query: str) -> OrchestratorResult:
    """
    Execute the full multi-step workflow for a query.
    Blocking — waits for all steps to complete.
    """
    t0 = time.time()
    graph = get_graph()

    initial_state: WorkflowState = {
        "query": query,
        "plan": [],
        "documents": [],
        "step_results": [],
        "final_output": "",
        "sources": [],
        "error": None,
        "metadata": {},
    }

    try:
        final_state = graph.invoke(initial_state)
    except Exception as e:
        logger.error(f"Workflow failed: {e}")
        return OrchestratorResult(
            output=f"Workflow failed: {e}",
            sources=[],
            steps=[],
            query=query,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    elapsed_ms = int((time.time() - t0) * 1000)

    return OrchestratorResult(
        output=final_state.get("final_output", ""),
        sources=final_state.get("sources", []),
        steps=final_state.get("step_results", []),
        query=query,
        elapsed_ms=elapsed_ms,
        plan=final_state.get("plan", []),
        metadata=final_state.get("metadata", {}),
    )


def stream_workflow(query: str) -> Generator[WorkflowEvent, None, None]:
    """
    Stream workflow progress as events.
    Use with FastAPI SSE endpoint.

    Yields WorkflowEvent objects.
    """
    t0 = time.time()

    try:
        graph = get_graph()
    except ImportError as e:
        yield WorkflowEvent("error", str(e))
        return

    initial_state: WorkflowState = {
        "query": query,
        "plan": [],
        "documents": [],
        "step_results": [],
        "final_output": "",
        "sources": [],
        "error": None,
        "metadata": {},
    }

    step_names = {
        "plan":     "Planning workflow…",
        "retrieve": "Retrieving relevant files…",
        "process":  "Processing documents…",
        "compile":  "Compiling final output…",
    }

    try:
        for node_name, state_update in graph.stream(initial_state):
            label = step_names.get(node_name, node_name)
            yield WorkflowEvent("step_start", {"step": node_name, "label": label})

            if node_name == "plan" and "plan" in state_update:
                yield WorkflowEvent("plan", {"steps": state_update["plan"]})

            elif node_name == "retrieve" and "documents" in state_update:
                docs = state_update["documents"]
                yield WorkflowEvent("step_done", {
                    "step": node_name,
                    "message": f"Found {len(docs)} relevant files",
                    "files": [d["filename"] for d in docs[:5]],
                })

            elif node_name == "process":
                results = state_update.get("step_results", [])
                for r in results:
                    if r.get("step") == "process":
                        yield WorkflowEvent("step_done", {
                            "step": node_name,
                            "message": f"Processed {r.get('docs_processed', 0)} files",
                            "agent": r.get("agent"),
                        })

            elif node_name == "compile" and "final_output" in state_update:
                output = state_update["final_output"]
                words = output.split()
                chunk_size = 8
                for i in range(0, len(words), chunk_size):
                    chunk = " ".join(words[i:i+chunk_size])
                    if i + chunk_size < len(words):
                        chunk += " "
                    yield WorkflowEvent("chunk", chunk)

        elapsed = int((time.time() - t0) * 1000)
        yield WorkflowEvent("done", {"elapsed_ms": elapsed})

    except Exception as e:
        logger.error(f"Workflow stream failed: {e}")
        yield WorkflowEvent("error", str(e))



_PLANNER_SYSTEM = """You are a workflow planner for a file search assistant.

Given a user query, output ONLY a JSON array of steps (no markdown):
["step1", "step2", "step3"]

Available steps: retrieve, summary, qa, email, media, compile
Rules:
- Always include "compile" as the last step for multi-doc workflows
- For report requests: ["retrieve", "summary", "compile"]
- For email requests: ["retrieve", "email"]
- For meeting analysis: ["retrieve", "media", "compile"]
- For question answering: ["retrieve", "qa"]
- Keep it minimal — 2-4 steps max"""


def _plan_workflow(query: str) -> list[str]:
    """Ask Gemini to plan the workflow steps."""
    import json
    import os
    import re
    from google import genai
    from google.genai import types
    from config import settings

    try:
        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"Plan this request: {query}",
            config=types.GenerateContentConfig(
                system_instruction=_PLANNER_SYSTEM,
                temperature=0.0,
                max_output_tokens=100,
            ),
        )
        text = re.sub(r"```(?:json)?|```", "", response.text or "").strip()
        steps = json.loads(text)
        if isinstance(steps, list) and steps:
            return [str(s) for s in steps]
    except Exception as e:
        logger.warning(f"Workflow planning failed: {e} — using default plan")

    return ["retrieve", "summary", "compile"]


_REPORT_SYSTEM = """You are a professional report writer.
Synthesize the provided content into a well-structured report.

Format:
# [Report Title]

## Executive Summary
[2-3 sentences]

## Key Findings
[Bullet points with the most important information]

## Detailed Analysis
[Organized by topic/document]

## Recommendations / Next Steps
[If applicable]

---
*Sources: [list filenames]*"""


def _report_prompt(query: str, combined: str, documents: list[dict]) -> str:
    filenames = ", ".join(d.get("filename", "") for d in documents[:8])
    return f"""User request: {query}

Sources: {filenames}

Content from all documents:
{combined[:8000]}

Compile this into a comprehensive report:"""


def _call_gemini_compile(prompt: str, system: str) -> str:
    import os
    from google import genai
    from google.genai import types
    from config import settings

    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=settings.gemini_llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
            max_output_tokens=2048,
        ),
    )
    return response.text or ""