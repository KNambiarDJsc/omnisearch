import React, { useState, useRef, useEffect } from "react";
import { Sparkles, FileText, Code, Image, Music, Video, File, Loader2, StopCircle } from "lucide-react";
import { streamCopilot, CopilotSource, openFile } from "../api";

interface CopilotPanelProps {
    initialQuery?: string;
}

function SourceIcon({ fileType }: { fileType: string }) {
    const t = fileType.toLowerCase();
    const cls = "flex-shrink-0";
    const sz = 12;
    if (["pdf", "docx", "txt", "md"].includes(t)) return <FileText size={sz} className={`${cls} text-blue-400`} />;
    if (["py", "js", "ts", "json"].includes(t)) return <Code size={sz} className={`${cls} text-emerald-400`} />;
    if (["png", "jpg", "jpeg"].includes(t)) return <Image size={sz} className={`${cls} text-purple-400`} />;
    if (["mp3", "wav"].includes(t)) return <Music size={sz} className={`${cls} text-orange-400`} />;
    if (["mp4", "mov"].includes(t)) return <Video size={sz} className={`${cls} text-pink-400`} />;
    return <File size={sz} className={`${cls} text-zinc-400`} />;
}

function SourceCard({ source }: { source: CopilotSource }) {
    return (
        <button
            onClick={() => openFile(source.file_path)}
            title={source.file_path}
            className="flex items-center gap-2 px-2.5 py-1.5 bg-zinc-800/60 hover:bg-zinc-700/60 border border-zinc-700/50 rounded-lg transition-colors text-left group max-w-[200px]"
        >
            <SourceIcon fileType={source.file_type} />
            <span className="text-[11px] text-zinc-400 group-hover:text-zinc-200 truncate transition-colors">
                {source.filename}
            </span>
        </button>
    );
}

// Minimal markdown rendering — bold, code, bullets
function AnswerText({ text }: { text: string }) {
    const lines = text.split("\n");
    return (
        <div className="text-sm text-zinc-200 leading-relaxed space-y-1">
            {lines.map((line, i) => {
                // Bullet points
                if (line.trim().startsWith("- ") || line.trim().startsWith("• ")) {
                    return (
                        <div key={i} className="flex gap-2 ml-2">
                            <span className="text-zinc-500 flex-shrink-0 mt-0.5">•</span>
                            <span>{renderInline(line.replace(/^[\s\-•]+/, ""))}</span>
                        </div>
                    );
                }
                // Numbered list
                if (/^\d+\.\s/.test(line.trim())) {
                    const [num, ...rest] = line.trim().split(/\.\s+/);
                    return (
                        <div key={i} className="flex gap-2 ml-2">
                            <span className="text-zinc-500 flex-shrink-0 tabular-nums">{num}.</span>
                            <span>{renderInline(rest.join(". "))}</span>
                        </div>
                    );
                }
                // Empty line
                if (!line.trim()) return <div key={i} className="h-1" />;
                // Normal paragraph
                return <p key={i}>{renderInline(line)}</p>;
            })}
        </div>
    );
}

function renderInline(text: string): React.ReactNode {
    // Bold: **text**
    const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**"))
            return <strong key={i} className="text-zinc-100 font-semibold">{part.slice(2, -2)}</strong>;
        if (part.startsWith("`") && part.endsWith("`"))
            return <code key={i} className="text-emerald-400 bg-zinc-800 px-1 rounded text-xs font-mono">{part.slice(1, -1)}</code>;
        return part;
    });
}

export function CopilotPanel({ initialQuery = "" }: CopilotPanelProps) {
    const [query, setQuery] = useState(initialQuery);
    const [answer, setAnswer] = useState("");
    const [sources, setSources] = useState<CopilotSource[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [elapsed, setElapsed] = useState<number | null>(null);
    const stopRef = useRef<(() => void) | null>(null);
    const answerRef = useRef<HTMLDivElement>(null);

    // Auto-scroll answer as it streams in
    useEffect(() => {
        if (answerRef.current) {
            answerRef.current.scrollTop = answerRef.current.scrollHeight;
        }
    }, [answer]);

    function handleAsk() {
        if (!query.trim() || isStreaming) return;

        setAnswer("");
        setSources([]);
        setIsStreaming(true);
        setElapsed(null);
        const t0 = Date.now();

        const stop = streamCopilot(query, (event) => {
            if (event.type === "sources") {
                setSources(event.data);
            } else if (event.type === "chunk") {
                setAnswer((prev) => prev + event.data);
            } else if (event.type === "done") {
                setIsStreaming(false);
                setElapsed(Date.now() - t0);
            } else if (event.type === "error") {
                setAnswer((prev) => prev + `\n\n[Error: ${event.data}]`);
                setIsStreaming(false);
            }
        });

        stopRef.current = stop;
    }

    function handleStop() {
        stopRef.current?.();
        setIsStreaming(false);
    }

    return (
        <div className="flex flex-col h-full">
            {/* Input */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
                <Sparkles size={15} className="text-violet-400 flex-shrink-0" />
                <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleAsk()}
                    placeholder="Ask anything about your files…"
                    className="flex-1 bg-transparent text-zinc-100 text-[14px] placeholder-zinc-600 outline-none"
                    autoFocus
                />
                {isStreaming ? (
                    <button
                        onClick={handleStop}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 rounded-lg text-xs transition-colors"
                    >
                        <StopCircle size={12} />
                        Stop
                    </button>
                ) : (
                    <button
                        onClick={handleAsk}
                        disabled={!query.trim()}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-xs font-medium transition-colors"
                    >
                        <Sparkles size={12} />
                        Ask
                    </button>
                )}
            </div>

            {/* Sources row */}
            {sources.length > 0 && (
                <div className="flex items-center gap-2 px-4 py-2 border-b border-zinc-800 overflow-x-auto">
                    <span className="text-[10px] text-zinc-600 uppercase tracking-wider flex-shrink-0">Sources</span>
                    {sources.map((s) => (
                        <SourceCard key={s.file_path} source={s} />
                    ))}
                </div>
            )}

            {/* Answer */}
            <div ref={answerRef} className="flex-1 overflow-y-auto px-4 py-4">
                {!answer && !isStreaming && (
                    <div className="flex flex-col items-center justify-center h-full text-zinc-700">
                        <Sparkles size={28} className="mb-3 opacity-30" />
                        <p className="text-sm text-center">Ask a question about your indexed files</p>
                        <div className="mt-4 grid grid-cols-1 gap-1.5 w-full max-w-xs">
                            {EXAMPLE_QUESTIONS.map((q) => (
                                <button
                                    key={q}
                                    onClick={() => setQuery(q)}
                                    className="text-[11px] text-zinc-600 hover:text-zinc-400 bg-zinc-800/40 hover:bg-zinc-800 border border-zinc-800 rounded-lg px-3 py-2 text-left transition-colors"
                                >
                                    {q}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {(answer || isStreaming) && (
                    <div>
                        <AnswerText text={answer} />
                        {isStreaming && (
                            <span className="inline-flex items-center gap-1.5 mt-2 text-xs text-zinc-600">
                                <Loader2 size={10} className="animate-spin" />
                                Thinking…
                            </span>
                        )}
                        {!isStreaming && elapsed && (
                            <p className="mt-3 text-[10px] text-zinc-700">
                                {sources.length} source{sources.length !== 1 ? "s" : ""} · {elapsed}ms
                            </p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

const EXAMPLE_QUESTIONS = [
    "What did we decide in the last meeting?",
    "Summarize the main research papers I have",
    "Find any invoices from this year",
    "What Python projects am I working on?",
];