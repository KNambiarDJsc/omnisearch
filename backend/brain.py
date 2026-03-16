"""
brain.py — OmniSearch FastAPI server.

Phases 1-6 endpoints:

  Indexing:
    POST /index              — single file
    POST /index-folder       — bulk folder (background)
    DELETE /index            — remove file from index

  Search:
    POST /search             — hybrid search (BM25 + vector + rerank)
    POST /search/vector      — vector-only (for comparison/testing)

  Copilot:
    POST /copilot            — RAG question answering (blocking)
    POST /copilot/stream     — RAG with SSE streaming

  Watcher:
    POST /watch              — add folder to live watcher
    DELETE /watch            — remove folder
    GET  /watched            — list watched folders

  System:
    GET  /status             — health + collection stats + pipeline info
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from embedder import embed_file, get_snippet, WATCHED_EXTENSIONS
from storage import upsert_file, delete_file, collection_stats
from search import hybrid_search, semantic_search
from watcher import FolderWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("brain")


# ── Watcher singleton ──────────────────────────────────────────────

def _sync_index_callback(file_path: str) -> None:
    try:
        _index_single_file(file_path)
    except Exception as e:
        logger.error(f"Auto-index failed for {file_path}: {e}")


watcher = FolderWatcher(index_callback=_sync_index_callback)


# ── Core indexing pipeline ─────────────────────────────────────────

def _index_single_file(file_path: str) -> dict:
    """
    Full indexing pipeline:
      file → parse → embed → upsert Qdrant → upsert BM25
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    if ext not in WATCHED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}")

    filename  = path.name
    file_type = ext.lstrip(".")
    snippet   = get_snippet(path)

    # Vector embedding → Qdrant
    embedding = embed_file(path)
    point_id = upsert_file(
        file_path=str(path.resolve()),
        embedding=embedding,
        filename=filename,
        file_type=file_type,
        snippet=snippet,
    )

    # BM25 index update
    try:
        from bm25_index import get_bm25_index
        get_bm25_index().upsert(
            file_path=str(path.resolve()),
            filename=filename,
            file_type=file_type,
            snippet=snippet,
        )
    except Exception as e:
        logger.warning(f"BM25 upsert failed for {filename}: {e}")

    logger.info(f"Indexed: {filename} (id={point_id[:8]}…)")
    return {
        "filename":  filename,
        "file_type": file_type,
        "snippet":   snippet[:100],
        "point_id":  point_id,
    }


# ── Lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load BM25 index on startup
    try:
        from bm25_index import get_bm25_index
        idx = get_bm25_index()
        logger.info(f"BM25 index ready: {idx.doc_count()} documents")
    except Exception as e:
        logger.warning(f"BM25 init failed: {e}")

    watcher.start()
    logger.info(f"OmniSearch backend live — http://localhost:{settings.port}")
    yield
    watcher.stop()
    logger.info("OmniSearch backend shutdown")


# ── App ────────────────────────────────────────────────────────────

app = FastAPI(
    title="OmniSearch API",
    description="Local multimodal semantic search — Phases 1-6",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response models ────────────────────────────────────────

class IndexFileRequest(BaseModel):
    file_path: str

class IndexFolderRequest(BaseModel):
    folder_path: str
    recursive: bool = True

class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    mode: str = "hybrid"   # "hybrid" | "vector"

class WatchFolderRequest(BaseModel):
    folder_path: str

class CopilotRequest(BaseModel):
    query: str
    top_k: Optional[int] = None


# ── Indexing endpoints ─────────────────────────────────────────────

@app.post("/index")
async def index_file(req: IndexFileRequest, background_tasks: BackgroundTasks):
    path = Path(req.file_path)
    if not path.exists():
        raise HTTPException(404, f"File not found: {req.file_path}")
    ext = path.suffix.lower()
    if ext not in WATCHED_EXTENSIONS:
        raise HTTPException(422, f"Unsupported type '{ext}'")

    background_tasks.add_task(_safe_index, req.file_path)
    return {"status": "queued", "file": path.name}


@app.post("/index-folder")
async def index_folder(req: IndexFolderRequest, background_tasks: BackgroundTasks):
    folder = Path(req.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(404, f"Folder not found: {req.folder_path}")

    def _run():
        pattern = "**/*" if req.recursive else "*"
        files = [
            f for f in folder.glob(pattern)
            if f.is_file() and f.suffix.lower() in WATCHED_EXTENSIONS
        ]
        logger.info(f"Indexing {len(files)} files in {folder}")
        ok, fail = 0, 0
        for f in files:
            try:
                _index_single_file(str(f))
                ok += 1
            except Exception as e:
                logger.warning(f"Skipped {f.name}: {e}")
                fail += 1
        logger.info(f"Folder index done: {ok} ok, {fail} failed")

    background_tasks.add_task(_run)
    return {"status": "queued", "folder": str(folder), "recursive": req.recursive}


@app.delete("/index")
async def delete_index(req: IndexFileRequest):
    try:
        delete_file(req.file_path)
        from bm25_index import get_bm25_index
        get_bm25_index().delete(req.file_path)
        return {"status": "deleted", "file": req.file_path}
    except Exception as e:
        raise HTTPException(500, str(e))


def _safe_index(file_path: str):
    try:
        _index_single_file(file_path)
    except Exception as e:
        logger.error(f"Background index failed: {e}")


# ── Search endpoints ───────────────────────────────────────────────

@app.post("/search")
async def search(req: SearchRequest):
    """
    Hybrid search: BM25 + vector → RRF merge → cross-encoder rerank → top_k.
    Set mode='vector' for vector-only (no BM25, no rerank).
    """
    if not req.query.strip():
        raise HTTPException(422, "Query cannot be empty")

    try:
        if req.mode == "vector":
            results = semantic_search(req.query, top_k=req.top_k)
        else:
            results = hybrid_search(req.query, top_k=req.top_k)

        return {
            "query":   req.query,
            "mode":    req.mode,
            "results": [r.to_dict() for r in results],
            "count":   len(results),
        }
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(500, f"Search failed: {e}")


# ── Copilot endpoints ──────────────────────────────────────────────

@app.post("/copilot")
async def copilot(req: CopilotRequest):
    """
    RAG question answering over indexed files.
    Blocking — waits for full Gemini response.
    Use /copilot/stream for streaming.
    """
    if not req.query.strip():
        raise HTTPException(422, "Query cannot be empty")

    try:
        from copilot import ask_copilot
        response = ask_copilot(req.query, top_k=req.top_k)
        return response.to_dict()
    except Exception as e:
        logger.error(f"Copilot error: {e}")
        raise HTTPException(500, f"Copilot failed: {e}")


@app.post("/copilot/stream")
async def copilot_stream(req: CopilotRequest):
    """
    Streaming RAG — SSE response.
    Yields: sources immediately, then answer chunks as they stream from Gemini.

    Event format:
      data: {"type": "sources", "data": [...]}
      data: {"type": "chunk",   "data": "text..."}
      data: {"type": "done",    "data": {"sources": [...]}}
    """
    if not req.query.strip():
        raise HTTPException(422, "Query cannot be empty")

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            from copilot import stream_copilot
            for event in stream_copilot(req.query, top_k=req.top_k):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"Copilot stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Agent endpoints (Phase 7 + 8) ────────────────────────────────

class AgentRequest(BaseModel):
    query: str
    agent: Optional[str] = None       # if None, auto-classify
    context: Optional[dict] = None    # extra context for the agent
    top_k: Optional[int] = None


@app.post("/agent")
async def run_agent_endpoint(req: AgentRequest):
    """
    Run a specific agent or auto-classify and route.

    If req.agent is set: run that agent directly.
    If req.agent is None: classify the query and route automatically.

    Returns AgentResult.
    """
    if not req.query.strip():
        raise HTTPException(422, "Query cannot be empty")

    try:
        # Auto-classify if no agent specified
        if not req.agent:
            from agents.classifier import classify_intent
            intent = classify_intent(req.query)
            agent_name = intent.agent
            logger.info(f"Classified '{req.query[:40]}' → {agent_name} ({intent.confidence:.0%})")
        else:
            agent_name = req.agent
            intent = None

        # Orchestrator goes to the workflow engine
        if agent_name == "orchestrator":
            from agents.orchestrator import run_workflow
            result = run_workflow(req.query)
            return {
                "agent": "orchestrator",
                "intent_confidence": getattr(intent, "confidence", 1.0),
                **result.to_dict(),
            }

        # Single agent run
        from agents.registry import run_agent
        ctx = req.context or {}
        if req.top_k:
            ctx["top_k"] = req.top_k

        result = run_agent(agent_name, req.query, ctx)
        return {
            **result.to_dict(),
            "intent_confidence": getattr(intent, "confidence", 1.0),
            "intent_reasoning": getattr(intent, "reasoning", ""),
        }

    except Exception as e:
        logger.error(f"Agent endpoint error: {e}")
        raise HTTPException(500, f"Agent failed: {e}")


@app.post("/agent/stream")
async def run_agent_stream(req: AgentRequest):
    """
    Streaming agent endpoint — SSE.
    Routes to orchestrator stream for multi-step queries.
    Single-agent queries stream the output word-by-word.

    Event format:
      {"type": "intent",      "data": {"agent": "...", "confidence": 0.9}}
      {"type": "plan",        "data": {"steps": [...]}}           // orchestrator only
      {"type": "step_start",  "data": {"step": "...", "label": "..."}}
      {"type": "step_done",   "data": {"step": "...", "message": "..."}}
      {"type": "chunk",       "data": "text chunk..."}
      {"type": "sources",     "data": [...]}
      {"type": "done",        "data": {"elapsed_ms": 1234}}
    """
    if not req.query.strip():
        raise HTTPException(422, "Query cannot be empty")

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            # Classify
            if not req.agent:
                from agents.classifier import classify_intent
                intent = classify_intent(req.query)
                agent_name = intent.agent
            else:
                agent_name = req.agent
                from agents.classifier import IntentResult
                intent = IntentResult(agent=agent_name, confidence=1.0, reasoning="explicit")

            yield f"data: {json.dumps({'type': 'intent', 'data': {'agent': agent_name, 'confidence': intent.confidence, 'reasoning': intent.reasoning}})}\n\n"

            # Orchestrator — full multi-step stream
            if agent_name == "orchestrator":
                from agents.orchestrator import stream_workflow, WorkflowEvent
                for event in stream_workflow(req.query):
                    yield event.to_sse()
                return

            # Single agent — run and stream output chunks
            yield f"data: {json.dumps({'type': 'step_start', 'data': {'step': agent_name, 'label': f'Running {agent_name} agent…'}})}\n\n"

            from agents.registry import run_agent
            ctx = req.context or {}
            if req.top_k:
                ctx["top_k"] = req.top_k

            result = run_agent(agent_name, req.query, ctx)

            # Emit sources
            if result.sources:
                yield f"data: {json.dumps({'type': 'sources', 'data': result.sources[:6]})}\n\n"

            # Stream the output in chunks
            words = result.output.split()
            chunk_size = 10
            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i:i+chunk_size])
                if i + chunk_size < len(words):
                    chunk += " "
                yield f"data: {json.dumps({'type': 'chunk', 'data': chunk})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'data': {'elapsed_ms': result.elapsed_ms, 'agent': agent_name}})}\n\n"

        except Exception as e:
            logger.error(f"Agent stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/agent/list")
async def list_agents():
    """List all available agents and their capabilities."""
    from agents.registry import AgentRegistry
    return {"agents": AgentRegistry.descriptions()}


@app.post("/agent/classify")
async def classify_query(req: AgentRequest):
    """Classify a query and return the routing decision without running it."""
    from agents.classifier import classify_intent
    intent = classify_intent(req.query)
    return {
        "query": req.query,
        "agent": intent.agent,
        "confidence": intent.confidence,
        "reasoning": intent.reasoning,
        "workflow_hint": intent.workflow_hint,
    }


# ── Watcher endpoints ──────────────────────────────────────────────

@app.post("/watch")
async def add_watch(req: WatchFolderRequest):
    if not watcher.watch_folder(req.folder_path):
        raise HTTPException(400, f"Invalid path or already watched: {req.folder_path}")
    return {"status": "watching", "folder": req.folder_path}


@app.delete("/watch")
async def remove_watch(req: WatchFolderRequest):
    if not watcher.unwatch_folder(req.folder_path):
        raise HTTPException(404, f"Not watching: {req.folder_path}")
    return {"status": "unwatched", "folder": req.folder_path}


@app.get("/watched")
async def get_watched():
    return {"folders": watcher.watched_folders()}


# ── Status ─────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    try:
        stats = collection_stats()
        from bm25_index import get_bm25_index
        from reranker import is_reranker_loaded
        bm25_count = get_bm25_index().doc_count()
    except Exception as e:
        return {"status": "degraded", "error": str(e)}

    from agents.registry import AgentRegistry
    return {
        "status": "ok",
        "version": "0.6.0",
        "collection": stats,
        "bm25_docs": bm25_count,
        "reranker_loaded": is_reranker_loaded(),
        "reranker_model": settings.reranker_model,
        "reranker_enabled": settings.reranker_enabled,
        "watched_folders": len(watcher.watched_folders()),
        "pipeline": "BM25 + vector → RRF → cross-encoder rerank",
        "agents": AgentRegistry.names(),
    }


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "brain:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )