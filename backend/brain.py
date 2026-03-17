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
from pydantic import BaseModel, SkipValidation

from config import settings
from embedder import embed_file, get_snippet, WATCHED_EXTENSIONS
from storage import upsert_file, delete_file, collection_stats
from search import hybrid_search, semantic_search
from watcher import FolderWatcher

if settings.gemini_api_key:
    os.environ["GEMINI_API_KEY"] = settings.gemini_api_key
else:
    print("❌ GEMINI_API_KEY still missing in settings")

print("DEBUG KEY:", settings.gemini_api_key[:10] if settings.gemini_api_key else "NOT FOUND")

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

    # Write metadata to SQLite
    try:
        from storage_manager import upsert_file_metadata, hash_embedding, _hash_file
        content_hash = _hash_file(path)
        emb_hash = hash_embedding(embedding)
        upsert_file_metadata(
            file_id=point_id,
            file_path=str(path.resolve()),
            filename=filename,
            file_type=file_type,
            size_bytes=path.stat().st_size,
            content_hash=content_hash,
            embedding_hash=emb_hash,
            qdrant_point=point_id,
        )
    except Exception as meta_err:
        logger.warning(f"SQLite metadata write failed for {filename}: {meta_err}")

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
    # Bootstrap OS-aware storage directories
    try:
        from storage_manager import bootstrap_storage, metadata_stats
        store_paths = bootstrap_storage()
        # Redirect config paths to OS app-data dir
        settings.qdrant_path = str(store_paths["qdrant"])
        settings.bm25_index_path = str(store_paths["bm25"] / "bm25_index.pkl")
        logger.info(f"Storage: {store_paths['root']}")
        db_stats = metadata_stats()
        logger.info(f"Metadata DB: {db_stats['files']} files, {db_stats['indexed']} indexed")
    except Exception as e:
        logger.warning(f"Storage manager init failed (using defaults): {e}")

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
    # Auto-snapshot on clean shutdown — never lose index state
    try:
        from snapshot import export_snapshot, prune_snapshots
        snap_stats = collection_stats()
        if (snap_stats.get("points_count") or 0) > 0:
            snap_path = export_snapshot(label="shutdown", compress=True)
            prune_snapshots(keep=10)
            logger.info(f"Auto-snapshot saved: {snap_path.split('/')[-1]}")
    except Exception as snap_err:
        logger.warning(f"Auto-snapshot failed (non-fatal): {snap_err}")

    # Optional cloud sync on shutdown
    if settings.enable_cloud_sync and settings.sync_on_shutdown:
        try:
            from cloud_sync import sync_embeddings_to_r2, sync_metadata_to_r2
            sync_embeddings_to_r2(settings.sync_user_id, delta_only=True)
            sync_metadata_to_r2(settings.sync_user_id)
            logger.info("Cloud sync on shutdown complete")
        except Exception as sync_err:
            logger.warning(f"Cloud sync on shutdown failed (non-fatal): {sync_err}")

    logger.info("OmniSearch backend shutdown")


# ── App ────────────────────────────────────────────────────────────

app = FastAPI(
    title="OmniSearch API",
    description="Local multimodal semantic search — Phase 8",
    version="0.6.0",
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


# ── Snapshot endpoints ────────────────────────────────────────────

class SnapshotExportRequest(BaseModel):
    label: str = ""
    compress: bool = False

class SnapshotImportRequest(BaseModel):
    snapshot_path: str
    merge: bool = True
    verify_checksum: bool = True

class SnapshotDiffRequest(BaseModel):
    path_a: str
    path_b: str

class SnapshotDeltaRequest(BaseModel):
    since_snapshot_path: str
    label: str = "delta"
    compress: bool = False

class SnapshotPruneRequest(BaseModel):
    keep: int = 5


@app.post("/snapshot/export")
async def snapshot_export(req: SnapshotExportRequest, background_tasks: BackgroundTasks):
    """
    Export all Qdrant vectors + BM25 metadata to a portable JSON snapshot.

    Returns immediately with the snapshot path.
    Use compress=true to gzip (~60% smaller, same restore time).
    """
    try:
        from snapshot import export_snapshot
        path = export_snapshot(label=req.label, compress=req.compress)
        import os
        size_kb = os.path.getsize(path) // 1024
        return {
            "status":   "ok",
            "path":     path,
            "filename": Path(path).name,
            "size_kb":  size_kb,
        }
    except Exception as e:
        logger.error(f"Snapshot export failed: {e}")
        raise HTTPException(500, f"Export failed: {e}")


@app.post("/snapshot/import")
async def snapshot_import(req: SnapshotImportRequest, background_tasks: BackgroundTasks):
    """
    Restore vectors from a snapshot into Qdrant + BM25.

    merge=true  → upsert on top of existing data (safe default)
    merge=false → wipe collection first, then import (full restore)

    Runs in background — returns immediately.
    Poll /status for updated points_count.
    """
    path = Path(req.snapshot_path)
    if not path.exists():
        raise HTTPException(404, f"Snapshot not found: {req.snapshot_path}")

    def _run():
        try:
            from snapshot import import_snapshot
            result = import_snapshot(
                req.snapshot_path,
                merge=req.merge,
                verify_checksum=req.verify_checksum,
            )
            logger.info(f"Snapshot import done: {result}")
        except Exception as e:
            logger.error(f"Snapshot import failed: {e}")

    background_tasks.add_task(_run)
    return {
        "status":   "importing",
        "snapshot": path.name,
        "merge":    req.merge,
    }


@app.post("/snapshot/diff")
async def snapshot_diff(req: SnapshotDiffRequest):
    """
    Compare two snapshots and return what changed between them.

    Great for auditing what was indexed in a time window,
    or figuring out the minimal delta to upload to cloud.
    """
    for p in [req.path_a, req.path_b]:
        if not Path(p).exists():
            raise HTTPException(404, f"Snapshot not found: {p}")
    try:
        from snapshot import diff_snapshots
        diff = diff_snapshots(req.path_a, req.path_b)
        return {
            "added":     len(diff.added),
            "removed":   len(diff.removed),
            "changed":   len(diff.changed),
            "unchanged": diff.unchanged,
            "summary":   diff.summary(),
            "added_paths":   diff.added[:20],    # first 20 for display
            "removed_paths": diff.removed[:20],
            "changed_paths": diff.changed[:20],
        }
    except Exception as e:
        raise HTTPException(500, f"Diff failed: {e}")


@app.post("/snapshot/delta")
async def snapshot_delta(req: SnapshotDeltaRequest):
    """
    Export only entries that changed since the given baseline snapshot.

    This is the efficient sync primitive — instead of re-uploading
    everything, export only what's new or modified.
    """
    if not Path(req.since_snapshot_path).exists():
        raise HTTPException(404, f"Base snapshot not found: {req.since_snapshot_path}")
    try:
        from snapshot import export_delta_snapshot
        path = export_delta_snapshot(
            since_snapshot_path=req.since_snapshot_path,
            label=req.label,
            compress=req.compress,
        )
        import os
        size_kb = os.path.getsize(path) // 1024
        return {
            "status":   "ok",
            "path":     path,
            "filename": Path(path).name,
            "size_kb":  size_kb,
        }
    except Exception as e:
        raise HTTPException(500, f"Delta export failed: {e}")


@app.get("/snapshot/list")
async def snapshot_list():
    """List all snapshots on disk, newest first."""
    from snapshot import list_snapshots
    snaps = list_snapshots()
    return {
        "snapshots": [
            {
                "filename":   s.filename,
                "path":       s.path,
                "created_at": s.created_at,
                "count":      s.count,
                "size_kb":    s.size_bytes // 1024,
                "compressed": s.compressed,
            }
            for s in snaps
        ],
        "total": len(snaps),
    }


@app.post("/snapshot/prune")
async def snapshot_prune(req: SnapshotPruneRequest):
    """Delete all but the N most recent snapshots."""
    if req.keep < 1:
        raise HTTPException(422, "keep must be >= 1")
    from snapshot import prune_snapshots
    deleted = prune_snapshots(keep=req.keep)
    return {
        "deleted": deleted,
        "count":   len(deleted),
        "kept":    req.keep,
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


# ── Cloud sync endpoints ──────────────────────────────────────────

class SyncPushRequest(BaseModel):
    user_id: Optional[str] = None
    delta_only: bool = True
    include_metadata: bool = True

class SyncPullRequest(BaseModel):
    user_id: Optional[str] = None
    snapshot_key: Optional[str] = None
    merge: bool = True


@app.post("/sync")
async def sync_push(req: SyncPushRequest, background_tasks: BackgroundTasks):
    """
    Push embeddings (+ optionally metadata) to Cloudflare R2.

    delta_only=true  → only upload entries changed since last snapshot (fast)
    delta_only=false → full snapshot upload (use after major re-index)

    Runs in background — returns immediately.
    """
    if not settings.enable_cloud_sync:
        raise HTTPException(403, "Cloud sync disabled. Set ENABLE_CLOUD_SYNC=true in .env")

    user_id = req.user_id or settings.sync_user_id

    def _run():
        try:
            from cloud_sync import sync_embeddings_to_r2, sync_metadata_to_r2
            result = sync_embeddings_to_r2(user_id, delta_only=req.delta_only)
            logger.info(f"Sync push result: {result}")
            if req.include_metadata:
                sync_metadata_to_r2(user_id)
        except Exception as e:
            logger.error(f"Background sync push failed: {e}")

    background_tasks.add_task(_run)
    return {
        "status":     "queued",
        "direction":  "push",
        "user_id":    user_id,
        "delta_only": req.delta_only,
    }


@app.post("/sync/pull")
async def sync_pull(req: SyncPullRequest, background_tasks: BackgroundTasks):
    """
    Pull the latest snapshot from R2 and restore into Qdrant.

    snapshot_key: specific R2 key to restore. If None, uses most recent.
    merge=true    → upsert on top of existing (safe)
    merge=false   → wipe first, then restore (full rebuild)

    Runs in background.
    """
    if not settings.enable_cloud_sync:
        raise HTTPException(403, "Cloud sync disabled. Set ENABLE_CLOUD_SYNC=true in .env")

    user_id = req.user_id or settings.sync_user_id

    def _run():
        try:
            from cloud_sync import pull_embeddings_from_r2
            result = pull_embeddings_from_r2(
                user_id,
                snapshot_key=req.snapshot_key,
                merge=req.merge,
            )
            logger.info(f"Sync pull result: {result}")
        except Exception as e:
            logger.error(f"Background sync pull failed: {e}")

    background_tasks.add_task(_run)
    return {
        "status":    "queued",
        "direction": "pull",
        "user_id":   user_id,
        "merge":     req.merge,
    }


@app.get("/sync/status")
async def sync_status():
    """
    Return cloud sync status: last push/pull times, R2 snapshot list.
    Does NOT contact R2 if cloud sync is disabled.
    """
    try:
        from cloud_sync import get_sync_status
        return get_sync_status(settings.sync_user_id)
    except Exception as e:
        return {"cloud_sync_enabled": settings.enable_cloud_sync, "error": str(e)}


@app.get("/sync/list")
async def sync_list_remote():
    """List all snapshots stored in R2 for the current user."""
    if not settings.enable_cloud_sync:
        raise HTTPException(403, "Cloud sync disabled")
    from cloud_sync import list_r2_snapshots
    return {"snapshots": list_r2_snapshots(settings.sync_user_id)}


@app.get("/metadata/stats")
async def metadata_stats_endpoint():
    """Return SQLite metadata database stats."""
    try:
        from storage_manager import metadata_stats
        return metadata_stats()
    except Exception as e:
        raise HTTPException(500, str(e))


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