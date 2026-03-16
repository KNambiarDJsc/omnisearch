import axios from "axios";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:5005";

const api = axios.create({ baseURL: BASE_URL, timeout: 30_000 });

// ── Types ──────────────────────────────────────────────────────────

export interface SearchResult {
    filename: string;
    file_path: string;
    file_type: string;
    snippet: string;
    score: number;
    bm25_score?: number;
    vector_score?: number;
    rerank_score?: number;
    indexed_at?: number;
}

export interface SearchResponse {
    query: string;
    mode: string;
    results: SearchResult[];
    count: number;
}

export interface CopilotSource {
    filename: string;
    file_path: string;
    file_type: string;
    snippet: string;
    relevance_score: number;
}

export interface CopilotResponse {
    answer: string;
    sources: CopilotSource[];
    query: string;
    model: string;
    elapsed_ms: number;
}

export interface AgentResult {
    agent_name: string;
    output: string;
    sources: Record<string, unknown>[];
    metadata: Record<string, unknown>;
    success: boolean;
    error?: string;
    elapsed_ms: number;
    intent_confidence?: number;
    intent_reasoning?: string;
    // orchestrator extra fields
    steps?: Record<string, unknown>[];
    plan?: string[];
}

export interface AgentInfo {
    name: string;
    description: string;
    capabilities: string[];
}

export interface IntentResult {
    query: string;
    agent: string;
    confidence: number;
    reasoning: string;
    workflow_hint: string[];
}

export interface StatusResponse {
    status: string;
    version: string;
    collection: {
        collection: string;
        vectors_count: number;
        points_count: number;
        dimension: number;
    };
    bm25_docs: number;
    reranker_loaded: boolean;
    reranker_model: string;
    reranker_enabled: boolean;
    watched_folders: number;
    pipeline: string;
    agents: string[];
}

// ── Stream event types ─────────────────────────────────────────────

export type CopilotStreamEvent =
    | { type: "sources"; data: CopilotSource[] }
    | { type: "chunk"; data: string }
    | { type: "done"; data: { sources: CopilotSource[] } }
    | { type: "error"; data: string };

export type AgentStreamEvent =
    | { type: "intent"; data: { agent: string; confidence: number; reasoning: string } }
    | { type: "plan"; data: { steps: string[] } }
    | { type: "step_start"; data: { step: string; label: string } }
    | { type: "step_done"; data: { step: string; message?: string; files?: string[] } }
    | { type: "sources"; data: Record<string, unknown>[] }
    | { type: "chunk"; data: string }
    | { type: "done"; data: { elapsed_ms: number; agent?: string } }
    | { type: "error"; data: string };

// ── SSE stream helper ──────────────────────────────────────────────

function createSSEStream<T>(
    url: string,
    body: object,
    onEvent: (event: T) => void,
): () => void {
    const controller = new AbortController();

    fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
    }).then(async (response) => {
        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        if (!reader) return;
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";
            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                try { onEvent(JSON.parse(line.slice(6)) as T); } catch { }
            }
        }
    }).catch((err) => {
        if (err.name !== "AbortError") {
            (onEvent as any)({ type: "error", data: String(err) });
        }
    });

    return () => controller.abort();
}

// ── Search ─────────────────────────────────────────────────────────

export async function search(
    query: string,
    topK?: number,
    mode: "hybrid" | "vector" = "hybrid",
): Promise<SearchResponse> {
    const { data } = await api.post<SearchResponse>("/search", { query, top_k: topK, mode });
    return data;
}

// ── Copilot ────────────────────────────────────────────────────────

export async function askCopilot(query: string, topK?: number): Promise<CopilotResponse> {
    const { data } = await api.post<CopilotResponse>(
        "/copilot",
        { query, top_k: topK },
        { timeout: 60_000 },
    );
    return data;
}

export function streamCopilot(
    query: string,
    onEvent: (e: CopilotStreamEvent) => void,
    topK?: number,
): () => void {
    return createSSEStream<CopilotStreamEvent>(
        `${BASE_URL}/copilot/stream`,
        { query, top_k: topK },
        onEvent,
    );
}

// ── Agents ─────────────────────────────────────────────────────────

export async function runAgent(
    query: string,
    agent?: string,
    context?: object,
    topK?: number,
): Promise<AgentResult> {
    const { data } = await api.post<AgentResult>(
        "/agent",
        { query, agent, context, top_k: topK },
        { timeout: 120_000 },
    );
    return data;
}

export function streamAgent(
    query: string,
    onEvent: (e: AgentStreamEvent) => void,
    agent?: string,
    topK?: number,
): () => void {
    return createSSEStream<AgentStreamEvent>(
        `${BASE_URL}/agent/stream`,
        { query, agent, top_k: topK },
        onEvent,
    );
}

export async function classifyIntent(query: string): Promise<IntentResult> {
    const { data } = await api.post<IntentResult>("/agent/classify", { query });
    return data;
}

export async function listAgents(): Promise<{ agents: AgentInfo[] }> {
    const { data } = await api.get("/agent/list");
    return data;
}

// ── Indexing ───────────────────────────────────────────────────────

export async function indexFile(filePath: string) {
    const { data } = await api.post("/index", { file_path: filePath });
    return data;
}

export async function indexFolder(folderPath: string, recursive = true) {
    const { data } = await api.post("/index-folder", { folder_path: folderPath, recursive });
    return data;
}

export async function watchFolder(folderPath: string) {
    const { data } = await api.post("/watch", { folder_path: folderPath });
    return data;
}

export async function unwatchFolder(folderPath: string) {
    const { data } = await api.delete("/watch", { data: { folder_path: folderPath } });
    return data;
}

export async function getWatchedFolders(): Promise<{ folders: string[] }> {
    const { data } = await api.get("/watched");
    return data;
}

export async function getStatus(): Promise<StatusResponse> {
    const { data } = await api.get<StatusResponse>("/status");
    return data;
}

// ── Helpers ────────────────────────────────────────────────────────

export function openFile(filePath: string): void {
    try {
        // @ts-ignore — Tauri API available in desktop build
        if (window.__TAURI__) {
            import("@tauri-apps/api/shell").then(({ open }) => open(filePath));
        }
    } catch { }
}

export function copyToClipboard(text: string): void {
    navigator.clipboard.writeText(text).catch(console.error);
}