import React, { useState, useRef, useEffect } from "react";
import {
    Zap, Search, HelpCircle, FileText, Mail, Video,
    Loader2, StopCircle, ChevronRight, CheckCircle2,
    Clock, AlertCircle
} from "lucide-react";
import { streamAgent, AgentStreamEvent, openFile } from "../api";

// ── Types ──────────────────────────────────────────────────────────

interface Source {
    filename: string;
    file_path: string;
    file_type: string;
    snippet?: string;
}

interface WorkflowStep {
    step: string;
    label: string;
    status: "pending" | "running" | "done" | "error";
    message?: string;
}

// ── Agent metadata ─────────────────────────────────────────────────

const AGENT_META: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    search: { icon: <Search size={12} />, color: "text-blue-400", label: "Search" },
    qa: { icon: <HelpCircle size={12} />, color: "text-emerald-400", label: "Q&A" },
    summary: { icon: <FileText size={12} />, color: "text-amber-400", label: "Summary" },
    email: { icon: <Mail size={12} />, color: "text-pink-400", label: "Email" },
    media: { icon: <Video size={12} />, color: "text-purple-400", label: "Media" },
    orchestrator: { icon: <Zap size={12} />, color: "text-orange-400", label: "Workflow" },
};

const STEP_LABELS: Record<string, string> = {
    plan: "Planning…",
    retrieve: "Retrieving files…",
    process: "Processing documents…",
    summary: "Summarizing…",
    qa: "Answering…",
    email: "Drafting email…",
    media: "Analyzing media…",
    compile: "Compiling report…",
};

// ── Sub-components ─────────────────────────────────────────────────

function IntentBadge({ agent, confidence }: { agent: string; confidence: number }) {
    const meta = AGENT_META[agent] ?? { icon: <Zap size={12} />, color: "text-zinc-400", label: agent };
    return (
        <div className={`flex items-center gap-1.5 px-2 py-1 bg-zinc-800 border border-zinc-700/50 rounded-full text-[11px] font-medium flex-shrink-0 ${meta.color}`}>
            {meta.icon}
            <span>{meta.label}</span>
            <span className="text-zinc-600 font-normal">{Math.round(confidence * 100)}%</span>
        </div>
    );
}

function StepRow({ step }: { step: WorkflowStep }) {
    const isRunning = step.status === "running";
    const isDone = step.status === "done";
    const isError = step.status === "error";
    const isPending = step.status === "pending";

    return (
        <div className={`flex items-center gap-1.5 text-[11px] transition-opacity ${isPending ? "opacity-30" : "opacity-100"}`}>
            {isRunning && <Loader2 size={10} className="animate-spin text-blue-400 flex-shrink-0" />}
            {isDone && <CheckCircle2 size={10} className="text-emerald-500 flex-shrink-0" />}
            {isError && <AlertCircle size={10} className="text-red-400 flex-shrink-0" />}
            {isPending && <Clock size={10} className="text-zinc-700 flex-shrink-0" />}
            <span className={isDone ? "text-zinc-500" : isRunning ? "text-zinc-200" : "text-zinc-700"}>
                {step.label}
            </span>
            {step.message && isDone && (
                <span className="text-zinc-700 ml-0.5 truncate max-w-[140px]">— {step.message}</span>
            )}
        </div>
    );
}

function SourcePill({ source }: { source: Source }) {
    const ext = source.file_type?.toUpperCase() || "FILE";
    return (
        <button
            onClick={() => openFile(source.file_path)}
            title={source.file_path}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800/60 hover:bg-zinc-700/60 border border-zinc-700/40 rounded-lg text-[11px] transition-colors flex-shrink-0 max-w-[170px]"
        >
            <span className="text-zinc-600 font-mono text-[9px]">{ext}</span>
            <span className="text-zinc-400 truncate">{source.filename}</span>
        </button>
    );
}

// Renders markdown-like text
function AnswerText({ text }: { text: string }) {
    return (
        <div className="text-sm text-zinc-200 leading-relaxed space-y-1.5">
            {text.split("\n").map((line, i) => {
                if (/^#{1,3}\s/.test(line))
                    return (
                        <h3 key={i} className="text-zinc-100 font-semibold mt-3 first:mt-0 text-[13px]">
                            {line.replace(/^#+\s/, "")}
                        </h3>
                    );
                if (/^[-•]\s/.test(line.trim()))
                    return (
                        <div key={i} className="flex gap-2 ml-2">
                            <span className="text-zinc-600 flex-shrink-0 mt-0.5">•</span>
                            <span>{line.trim().replace(/^[-•]\s/, "")}</span>
                        </div>
                    );
                if (/^\d+\.\s/.test(line.trim())) {
                    const match = line.trim().match(/^(\d+)\.\s+(.*)$/);
                    return match ? (
                        <div key={i} className="flex gap-2 ml-2">
                            <span className="text-zinc-600 flex-shrink-0 tabular-nums">{match[1]}.</span>
                            <span>{match[2]}</span>
                        </div>
                    ) : <p key={i}>{line}</p>;
                }
                if (!line.trim()) return <div key={i} className="h-0.5" />;
                return (
                    <p key={i}>
                        {line.split(/(\*\*[^*]+\*\*|`[^`]+`)/).map((part, j) => {
                            if (part.startsWith("**") && part.endsWith("**"))
                                return <strong key={j} className="text-zinc-100 font-semibold">{part.slice(2, -2)}</strong>;
                            if (part.startsWith("`") && part.endsWith("`"))
                                return <code key={j} className="text-emerald-400 bg-zinc-800 px-1 rounded text-xs font-mono">{part.slice(1, -1)}</code>;
                            return part;
                        })}
                    </p>
                );
            })}
        </div>
    );
}

// ── Main component ─────────────────────────────────────────────────

export function AgentPanel() {
    const [query, setQuery] = useState("");
    const [output, setOutput] = useState("");
    const [sources, setSources] = useState<Source[]>([]);
    const [steps, setSteps] = useState<WorkflowStep[]>([]);
    const [agentName, setAgentName] = useState<string | null>(null);
    const [confidence, setConfidence] = useState<number>(0);
    const [isStreaming, setIsStreaming] = useState(false);
    const [elapsed, setElapsed] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);

    const stopRef = useRef<(() => void) | null>(null);
    const outputRef = useRef<HTMLDivElement>(null);

    // Auto-scroll as output streams in
    useEffect(() => {
        if (outputRef.current) {
            outputRef.current.scrollTop = outputRef.current.scrollHeight;
        }
    }, [output]);

    function handleRun() {
        if (!query.trim() || isStreaming) return;

        // Reset state
        setOutput("");
        setSources([]);
        setSteps([]);
        setAgentName(null);
        setConfidence(0);
        setElapsed(null);
        setError(null);
        setIsStreaming(true);

        const stop = streamAgent(query, handleEvent);
        stopRef.current = stop;
    }

    function handleEvent(ev: AgentStreamEvent) {
        switch (ev.type) {
            case "intent":
                setAgentName(ev.data.agent);
                setConfidence(ev.data.confidence);
                break;

            case "plan":
                setSteps(
                    ev.data.steps.map((s: string) => ({
                        step: s,
                        label: STEP_LABELS[s] ?? s,
                        status: "pending" as const,
                    }))
                );
                break;

            case "step_start":
                setSteps(prev => {
                    const exists = prev.some(s => s.step === ev.data.step);
                    const updated: WorkflowStep = {
                        step: ev.data.step,
                        label: ev.data.label || STEP_LABELS[ev.data.step] || ev.data.step,
                        status: "running",
                    };
                    if (exists) return prev.map(s => s.step === ev.data.step ? updated : s);
                    return [...prev, updated];
                });
                break;

            case "step_done":
                setSteps(prev =>
                    prev.map(s =>
                        s.step === ev.data.step
                            ? { ...s, status: "done", message: ev.data.message ?? "" }
                            : s
                    )
                );
                break;

            case "sources":
                setSources((ev.data as unknown as Source[]).slice(0, 6));
                break;

            case "chunk":
                setOutput(prev => prev + ev.data);
                break;

            case "done":
                setElapsed(ev.data?.elapsed_ms ?? null);
                setIsStreaming(false);
                // Ensure all steps show as done
                setSteps(prev => prev.map(s => ({ ...s, status: "done" as const })));
                break;

            case "error":
                setError(String(ev.data));
                setIsStreaming(false);
                break;
        }
    }

    function handleStop() {
        stopRef.current?.();
        setIsStreaming(false);
    }

    const hasActivity = agentName || steps.length > 0;
    const hasOutput = output.length > 0 || isStreaming;

    return (
        <div className="flex flex-col h-full min-h-0">

            {/* ── Input bar ─────────────────────────────────────────────── */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800 flex-shrink-0">
                <Zap size={14} className="text-orange-400 flex-shrink-0" />
                <input
                    type="text"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleRun()}
                    placeholder="Ask anything — agents are auto-selected…"
                    className="flex-1 bg-transparent text-zinc-100 text-[14px] placeholder-zinc-600 outline-none min-w-0"
                    autoFocus
                />
                {isStreaming ? (
                    <button
                        onClick={handleStop}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 rounded-lg text-xs transition-colors flex-shrink-0"
                    >
                        <StopCircle size={12} /> Stop
                    </button>
                ) : (
                    <button
                        onClick={handleRun}
                        disabled={!query.trim()}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-600 hover:bg-orange-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-xs font-medium transition-colors flex-shrink-0"
                    >
                        <Zap size={12} /> Run
                    </button>
                )}
            </div>

            {/* ── Intent + workflow steps bar ────────────────────────────── */}
            {hasActivity && (
                <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-zinc-900/60 overflow-x-auto flex-shrink-0 min-h-[36px]">
                    {agentName && <IntentBadge agent={agentName} confidence={confidence} />}
                    {steps.length > 0 && (
                        <div className="flex items-center gap-2 flex-shrink-0">
                            {steps.map((s, i) => (
                                <React.Fragment key={s.step}>
                                    {i > 0 && <ChevronRight size={9} className="text-zinc-700 flex-shrink-0" />}
                                    <StepRow step={s} />
                                </React.Fragment>
                            ))}
                        </div>
                    )}
                    {isStreaming && !steps.length && (
                        <div className="flex items-center gap-1.5 text-[11px] text-zinc-600">
                            <Loader2 size={10} className="animate-spin" />
                            Routing…
                        </div>
                    )}
                </div>
            )}

            {/* ── Sources row ────────────────────────────────────────────── */}
            {sources.length > 0 && (
                <div className="flex items-center gap-2 px-4 py-2 border-b border-zinc-800 overflow-x-auto flex-shrink-0">
                    <span className="text-[10px] text-zinc-600 uppercase tracking-wider font-medium flex-shrink-0">
                        Sources
                    </span>
                    {sources.map(s => <SourcePill key={s.file_path} source={s} />)}
                </div>
            )}

            {/* ── Output area ─────────────────────────────────────────────── */}
            <div ref={outputRef} className="flex-1 overflow-y-auto px-4 py-4 min-h-0">

                {/* Error */}
                {error && (
                    <div className="flex items-start gap-2 text-red-400 text-xs bg-red-950/30 border border-red-900/30 rounded-lg px-3 py-2 mb-3">
                        <AlertCircle size={12} className="flex-shrink-0 mt-0.5" />
                        <span>{error}</span>
                    </div>
                )}

                {/* Empty state with example prompts */}
                {!hasOutput && !error && (
                    <div className="flex flex-col items-center justify-center h-full text-zinc-700 py-4">
                        <Zap size={24} className="mb-3 opacity-20" />
                        <p className="text-xs text-center mb-4">
                            Automatically routes to the best agent
                        </p>

                        {/* Agent legend */}
                        <div className="flex flex-wrap gap-2 justify-center mb-5">
                            {Object.entries(AGENT_META).map(([key, meta]) => (
                                <div key={key} className={`flex items-center gap-1 text-[10px] ${meta.color} opacity-60`}>
                                    {meta.icon} {meta.label}
                                </div>
                            ))}
                        </div>

                        {/* Example prompts */}
                        <div className="grid grid-cols-2 gap-1.5 w-full max-w-sm">
                            {EXAMPLE_PROMPTS.map(ex => (
                                <button
                                    key={ex}
                                    onClick={() => setQuery(ex)}
                                    className="text-[11px] text-zinc-600 hover:text-zinc-400 bg-zinc-800/40 hover:bg-zinc-800 border border-zinc-800/50 rounded-lg px-3 py-2 text-left transition-colors leading-snug"
                                >
                                    {ex}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Streaming indicator with no output yet */}
                {isStreaming && !output && !error && (
                    <div className="flex items-center gap-2 text-zinc-600 text-xs">
                        <Loader2 size={11} className="animate-spin" />
                        Working…
                    </div>
                )}

                {/* Answer output */}
                {output && (
                    <div>
                        <AnswerText text={output} />
                        {isStreaming && (
                            <span className="inline-block w-1.5 h-3.5 bg-zinc-400 ml-1 animate-pulse rounded-sm" />
                        )}
                        {!isStreaming && elapsed !== null && (
                            <p className="mt-4 text-[10px] text-zinc-700 border-t border-zinc-800 pt-2">
                                {sources.length > 0 && `${sources.length} source${sources.length !== 1 ? "s" : ""} · `}
                                {agentName && `${AGENT_META[agentName]?.label ?? agentName} agent · `}
                                {elapsed}ms
                            </p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

const EXAMPLE_PROMPTS = [
    "Prepare a report from my research papers",
    "Write an email from the meeting notes",
    "What was decided in the last sync?",
    "Summarize all ML-related files",
    "Analyze the meeting recording",
    "Find files about product roadmap",
];