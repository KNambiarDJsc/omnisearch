import React, { useState, useEffect, useCallback, useRef } from "react";
import { Settings2, Sparkles, AlertCircle, Zap, Archive, Cloud } from "lucide-react";
import { SearchBar } from "./components/SearchBar.tsx";
import { ResultsList } from "./components/ResultsList.tsx";
import { Footer } from "./components/Footer.tsx";
import { IndexPanel } from "./components/IndexPanel.tsx";
import { CopilotPanel } from "./components/CopilotPanel.tsx";
import { AgentPanel } from "./components/AgentPanel.tsx";
import { SnapshotPanel } from "./components/SnapshotPanel.tsx";
import { CloudSyncPanel } from "./components/CloudSyncPanel.tsx";
import { search, getStatus, SearchResult, openFile } from "./api.ts";

const DEBOUNCE_MS = 260;
const MIN_QUERY_LEN = 2;

type View = "search" | "copilot" | "agent" | "index" | "snapshot" | "cloud";

export default function App() {
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [view, setView] = useState<View>("search");
    const [backendOnline, setBackendOnline] = useState(false);
    const [indexedCount, setIndexedCount] = useState(0);
    const [backendError, setBackendError] = useState<string | null>(null);

    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // ── Backend health poll ──────────────────────────────────────────
    useEffect(() => {
        async function checkStatus() {
            try {
                const s = await getStatus();
                setBackendOnline(true);
                setIndexedCount(s.collection?.points_count ?? 0);
                setBackendError(null);
            } catch {
                setBackendOnline(false);
                setBackendError("Backend offline — run: python brain.py");
            }
        }
        checkStatus();
        const interval = setInterval(checkStatus, 8000);
        return () => clearInterval(interval);
    }, []);

    // ── Debounced search ─────────────────────────────────────────────
    useEffect(() => {
        if (view !== "search") return;
        if (debounceRef.current) clearTimeout(debounceRef.current);

        if (query.trim().length < MIN_QUERY_LEN) {
            setResults([]);
            setSelectedIndex(0);
            setIsLoading(false);
            return;
        }

        setIsLoading(true);
        debounceRef.current = setTimeout(async () => {
            if (!backendOnline) { setIsLoading(false); return; }
            try {
                const res = await search(query, 10, "hybrid");
                setResults(res.results);
                setSelectedIndex(0);
            } catch {
                setResults([]);
            } finally {
                setIsLoading(false);
            }
        }, DEBOUNCE_MS);

        return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
    }, [query, backendOnline, view]);

    // ── Keyboard navigation ──────────────────────────────────────────
    const handleKeyDown = useCallback((e: KeyboardEvent) => {
        const mod = e.metaKey || e.ctrlKey;

        if (e.key === "Escape") {
            if (query) setQuery("");
            else if (view !== "search") setView("search");
            return;
        }

        // Global shortcuts — work from any view
        if (mod && e.key === "k") { e.preventDefault(); setView("copilot"); return; }
        if (mod && e.key === "j") { e.preventDefault(); setView("agent"); return; }
        if (mod && e.key === ",") { e.preventDefault(); setView("index"); return; }
        if (mod && e.key === "b") { e.preventDefault(); setView("snapshot"); return; }
        if (mod && e.key === "l") { e.preventDefault(); setView("cloud"); return; }

        if (view !== "search") return;

        if (e.key === "ArrowDown") {
            e.preventDefault();
            setSelectedIndex(i => Math.min(i + 1, results.length - 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSelectedIndex(i => Math.max(i - 1, 0));
        } else if (e.key === "Enter") {
            const sel = results[selectedIndex];
            if (sel) { e.preventDefault(); openFile(sel.file_path); }
        } else if (mod && e.key === "c") {
            const sel = results[selectedIndex];
            if (sel) { e.preventDefault(); navigator.clipboard.writeText(sel.file_path).catch(console.error); }
        }
    }, [query, view, results, selectedIndex]);

    useEffect(() => {
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [handleKeyDown]);

    // ── Render ───────────────────────────────────────────────────────
    return (
        <div className="flex items-center justify-center min-h-screen bg-black/20">
            <div
                className="w-[800px] h-[560px] flex flex-col bg-zinc-900 rounded-2xl border border-zinc-700/50 shadow-2xl shadow-black/80 overflow-hidden"
                style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}
            >
                {view === "search" && (
                    <>
                        <div className="flex items-center border-b border-zinc-800">
                            <div className="flex-1">
                                <SearchBar
                                    value={query}
                                    onChange={setQuery}
                                    isLoading={isLoading}
                                    placeholder={backendOnline ? "Search your computer…" : "Backend offline — start python brain.py"}
                                />
                            </div>
                            <div className="flex items-center gap-0.5 mx-2">
                                <NavBtn icon={<Sparkles size={14} />} label="Copilot (⌘K)" onClick={() => setView("copilot")} />
                                <NavBtn icon={<Zap size={14} />} label="Agents (⌘J)" onClick={() => setView("agent")} />
                                <NavBtn icon={<Settings2 size={14} />} label="Index (⌘,)" onClick={() => setView("index")} />
                                <NavBtn icon={<Archive size={14} />} label="Snapshots (⌘B)" onClick={() => setView("snapshot")} />
                                <NavBtn icon={<Cloud size={14} />} label="Cloud Sync (⌘L)" onClick={() => setView("cloud")} />
                            </div>
                        </div>

                        {backendError && (
                            <div className="flex items-center gap-2 px-4 py-2 bg-red-950/40 border-b border-red-900/40 text-red-400 text-xs">
                                <AlertCircle size={12} /> {backendError}
                            </div>
                        )}

                        <ResultsList
                            results={results}
                            selectedIndex={selectedIndex}
                            onSelectIndex={setSelectedIndex}
                            query={query}
                            isLoading={isLoading}
                        />
                        <Footer
                            results={results}
                            selectedIndex={selectedIndex}
                            backendOnline={backendOnline}
                            indexedCount={indexedCount}
                        />
                    </>
                )}

                {view === "copilot" && (
                    <>
                        <ViewHeader
                            icon={<Sparkles size={14} className="text-violet-400" />}
                            title="Copilot"
                            subtitle="Ask questions about your files"
                            onBack={() => setView("search")}
                            shortcut="⌘K"
                        />
                        <CopilotPanel />
                    </>
                )}

                {view === "agent" && (
                    <>
                        <ViewHeader
                            icon={<Zap size={14} className="text-orange-400" />}
                            title="Agents"
                            subtitle="Auto-routed task execution"
                            onBack={() => setView("search")}
                            shortcut="⌘J"
                        />
                        <AgentPanel />
                    </>
                )}

                {view === "index" && (
                    <>
                        <ViewHeader
                            icon={<Settings2 size={14} className="text-zinc-400" />}
                            title="Index Folders"
                            subtitle="Add folders to search"
                            onBack={() => setView("search")}
                            shortcut="⌘,"
                        />
                        <IndexPanel onClose={() => setView("search")} />
                    </>
                )}

                {view === "snapshot" && (
                    <>
                        <ViewHeader
                            icon={<Archive size={14} className="text-zinc-400" />}
                            title="Snapshots"
                            subtitle="Export · Restore · Diff"
                            onBack={() => setView("search")}
                            shortcut="⌘B"
                        />
                        <SnapshotPanel />
                    </>
                )}

                {view === "cloud" && (
                    <>
                        <ViewHeader
                            icon={<Cloud size={14} className="text-blue-400" />}
                            title="Cloud Sync"
                            subtitle="Cloudflare R2 backup"
                            onBack={() => setView("search")}
                            shortcut="⌘L"
                        />
                        <CloudSyncPanel />
                    </>
                )}
            </div>
        </div>
    );
}

// ── Sub-components ─────────────────────────────────────────────────

function NavBtn({ icon, label, onClick }: {
    icon: React.ReactNode; label: string; onClick: () => void;
}) {
    return (
        <button
            onClick={onClick}
            title={label}
            className="p-1.5 rounded-md text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
        >
            {icon}
        </button>
    );
}

function ViewHeader({ icon, title, subtitle, onBack, shortcut }: {
    icon: React.ReactNode;
    title: string;
    subtitle: string;
    onBack: () => void;
    shortcut?: string;
}) {
    return (
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 flex-shrink-0">
            <div className="flex items-center gap-2">
                {icon}
                <span className="text-sm font-medium text-zinc-200">{title}</span>
                <span className="text-xs text-zinc-600">{subtitle}</span>
            </div>
            <div className="flex items-center gap-2">
                {shortcut && (
                    <span className="text-[10px] text-zinc-700 font-mono">{shortcut}</span>
                )}
                <button
                    onClick={onBack}
                    className="text-xs text-zinc-500 hover:text-zinc-300 px-2 py-0.5 rounded border border-zinc-700 transition-colors"
                >
                    ← back
                </button>
            </div>
        </div>
    );
}