import { useState, useEffect } from "react";
import {
    Download, Upload, List, GitCompare, Trash2,
    CheckCircle2, AlertCircle, Loader2, RefreshCw,
    Archive, Zap
} from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:5005";

interface SnapshotInfo {
    filename: string;
    path: string;
    created_at: string;
    count: number;
    size_kb: number;
    compressed: boolean;
}

interface ToastItem {
    id: number;
    message: string;
    type: "success" | "error" | "info";
}

function formatDate(iso: string): string {
    try {
        return new Date(iso).toLocaleString();
    } catch { return iso; }
}

function Toast({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
    return (
        <div className="fixed bottom-4 right-4 space-y-2 z-50">
            {toasts.map(t => (
                <div
                    key={t.id}
                    onClick={() => onDismiss(t.id)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium cursor-pointer shadow-lg
            ${t.type === "success" ? "bg-emerald-900/90 text-emerald-300 border border-emerald-800" :
                            t.type === "error" ? "bg-red-900/90 text-red-300 border border-red-800" :
                                "bg-zinc-800 text-zinc-300 border border-zinc-700"}`}
                >
                    {t.type === "success" && <CheckCircle2 size={12} />}
                    {t.type === "error" && <AlertCircle size={12} />}
                    {t.type === "info" && <Zap size={12} />}
                    {t.message}
                </div>
            ))}
        </div>
    );
}

export function SnapshotPanel() {
    const [snapshots, setSnapshots] = useState<SnapshotInfo[]>([]);
    const [loading, setLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [importing, setImporting] = useState(false);
    const [pruning, setPruning] = useState(false);
    const [compress, setCompress] = useState(true);
    const [label, setLabel] = useState("");
    const [importPath, setImportPath] = useState("");
    const [merge, setMerge] = useState(true);
    const [selectedA, setSelectedA] = useState("");
    const [selectedB, setSelectedB] = useState("");
    const [diffResult, setDiffResult] = useState<any>(null);
    const [diffLoading, setDiffLoading] = useState(false);
    const [toasts, setToasts] = useState<ToastItem[]>([]);
    const [activeTab, setActiveTab] = useState<"export" | "import" | "list" | "diff">("list");

    useEffect(() => { loadSnapshots(); }, []);

    function addToast(message: string, type: ToastItem["type"] = "info") {
        const id = Date.now();
        setToasts(t => [...t, { id, message, type }]);
        setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4000);
    }

    async function loadSnapshots() {
        setLoading(true);
        try {
            const res = await fetch(`${BASE_URL}/snapshot/list`);
            const data = await res.json();
            setSnapshots(data.snapshots || []);
        } catch (e) {
            addToast("Failed to load snapshots", "error");
        } finally {
            setLoading(false);
        }
    }

    async function handleExport() {
        setExporting(true);
        try {
            const res = await fetch(`${BASE_URL}/snapshot/export`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label, compress }),
            });
            const data = await res.json();
            if (data.status === "ok") {
                addToast(`Exported: ${data.filename} (${data.size_kb} KB)`, "success");
                setLabel("");
                loadSnapshots();
            } else {
                addToast("Export failed", "error");
            }
        } catch (e) {
            addToast(`Export error: ${e}`, "error");
        } finally {
            setExporting(false);
        }
    }

    async function handleImport() {
        if (!importPath.trim()) return;
        setImporting(true);
        try {
            const res = await fetch(`${BASE_URL}/snapshot/import`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ snapshot_path: importPath, merge }),
            });
            const data = await res.json();
            if (data.status === "importing") {
                addToast(`Importing ${data.snapshot} in background…`, "info");
                setImportPath("");
            } else {
                addToast("Import failed", "error");
            }
        } catch (e) {
            addToast(`Import error: ${e}`, "error");
        } finally {
            setImporting(false);
        }
    }

    async function handleDiff() {
        if (!selectedA || !selectedB) return;
        setDiffLoading(true);
        setDiffResult(null);
        try {
            const res = await fetch(`${BASE_URL}/snapshot/diff`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path_a: selectedA, path_b: selectedB }),
            });
            const data = await res.json();
            setDiffResult(data);
        } catch (e) {
            addToast(`Diff error: ${e}`, "error");
        } finally {
            setDiffLoading(false);
        }
    }

    async function handlePrune() {
        setPruning(true);
        try {
            const res = await fetch(`${BASE_URL}/snapshot/prune`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keep: 5 }),
            });
            const data = await res.json();
            addToast(`Pruned ${data.count} old snapshots`, "success");
            loadSnapshots();
        } catch (e) {
            addToast("Prune failed", "error");
        } finally {
            setPruning(false);
        }
    }

    const tabs = [
        { key: "list", label: "Snapshots", icon: <List size={13} /> },
        { key: "export", label: "Export", icon: <Download size={13} /> },
        { key: "import", label: "Restore", icon: <Upload size={13} /> },
        { key: "diff", label: "Diff", icon: <GitCompare size={13} /> },
    ] as const;

    return (
        <div className="flex flex-col h-full min-h-0">
            {/* Tab bar */}
            <div className="flex items-center gap-0.5 px-4 py-2 border-b border-zinc-800 flex-shrink-0">
                {tabs.map(tab => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key)}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition-colors ${activeTab === tab.key
                            ? "bg-zinc-700 text-zinc-100"
                            : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
                            }`}
                    >
                        {tab.icon} {tab.label}
                    </button>
                ))}
                <button
                    onClick={loadSnapshots}
                    className="ml-auto p-1.5 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors"
                    title="Refresh"
                >
                    <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
                </button>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 min-h-0 space-y-4">

                {/* ── LIST ─────────────────────────────────────── */}
                {activeTab === "list" && (
                    <>
                        {snapshots.length === 0 && !loading && (
                            <div className="flex flex-col items-center justify-center py-10 text-zinc-700">
                                <Archive size={28} className="mb-3 opacity-30" />
                                <p className="text-sm">No snapshots yet</p>
                                <p className="text-xs mt-1">Export one to get started</p>
                            </div>
                        )}

                        {snapshots.map(snap => (
                            <div
                                key={snap.filename}
                                className="flex items-center justify-between gap-3 p-3 bg-zinc-800/40 border border-zinc-700/50 rounded-lg"
                            >
                                <div className="min-w-0">
                                    <div className="flex items-center gap-2 mb-0.5">
                                        <span className="text-[12px] font-mono text-zinc-200 truncate">{snap.filename}</span>
                                        {snap.compressed && (
                                            <span className="text-[9px] bg-zinc-700 text-zinc-400 px-1.5 py-0.5 rounded font-mono">gz</span>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-3 text-[10px] text-zinc-600">
                                        <span>{formatDate(snap.created_at)}</span>
                                        <span>{snap.count.toLocaleString()} vectors</span>
                                        <span>{snap.size_kb} KB</span>
                                    </div>
                                </div>
                                <button
                                    onClick={() => { setImportPath(snap.path); setActiveTab("import"); }}
                                    className="text-[11px] text-zinc-500 hover:text-zinc-200 px-2 py-1 rounded border border-zinc-700 hover:border-zinc-500 transition-colors flex-shrink-0"
                                >
                                    Restore
                                </button>
                            </div>
                        ))}

                        {snapshots.length > 5 && (
                            <button
                                onClick={handlePrune}
                                disabled={pruning}
                                className="flex items-center gap-2 text-xs text-zinc-600 hover:text-red-400 transition-colors"
                            >
                                {pruning ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                Prune — keep only 5 most recent
                            </button>
                        )}
                    </>
                )}

                {/* ── EXPORT ───────────────────────────────────── */}
                {activeTab === "export" && (
                    <div className="space-y-4">
                        <p className="text-xs text-zinc-500 leading-relaxed">
                            Exports all {" "}<span className="text-zinc-300 font-medium">vectors + metadata</span>{" "}
                            from Qdrant to a portable JSON file. Use this before migrations, to enable cloud sync,
                            or just as a safety backup.
                        </p>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase tracking-wider mb-1.5 block">
                                Label (optional)
                            </label>
                            <input
                                type="text"
                                value={label}
                                onChange={e => setLabel(e.target.value)}
                                placeholder="e.g. pre-migration, weekly-backup"
                                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-zinc-500 transition-colors"
                            />
                        </div>

                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={compress}
                                onChange={e => setCompress(e.target.checked)}
                                className="rounded"
                            />
                            <span className="text-xs text-zinc-400">
                                Compress with gzip{" "}
                                <span className="text-zinc-600">(~60% smaller, recommended)</span>
                            </span>
                        </label>

                        <button
                            onClick={handleExport}
                            disabled={exporting}
                            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
                        >
                            {exporting
                                ? <><Loader2 size={14} className="animate-spin" /> Exporting…</>
                                : <><Download size={14} /> Export Snapshot</>
                            }
                        </button>

                        <div className="text-[10px] text-zinc-700 space-y-0.5 pt-2 border-t border-zinc-800">
                            <p>• Snapshot saved to <code className="text-zinc-500">storage/snapshots/</code></p>
                            <p>• Backend auto-snapshots on clean shutdown</p>
                            <p>• Use diff to see what changed between snapshots</p>
                        </div>
                    </div>
                )}

                {/* ── IMPORT ───────────────────────────────────── */}
                {activeTab === "import" && (
                    <div className="space-y-4">
                        <p className="text-xs text-zinc-500 leading-relaxed">
                            Restore vectors from a snapshot. Use <span className="text-zinc-300">merge</span> to
                            add on top of existing data, or disable it to wipe and rebuild from scratch.
                        </p>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase tracking-wider mb-1.5 block">
                                Snapshot path
                            </label>
                            <input
                                type="text"
                                value={importPath}
                                onChange={e => setImportPath(e.target.value)}
                                placeholder="/path/to/snapshot_20250316_120000.json.gz"
                                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-xs font-mono text-zinc-100 placeholder-zinc-600 outline-none focus:border-zinc-500 transition-colors"
                            />
                        </div>

                        {snapshots.length > 0 && (
                            <div>
                                <label className="text-xs text-zinc-600 mb-1 block">Or pick from existing:</label>
                                <select
                                    value={importPath}
                                    onChange={e => setImportPath(e.target.value)}
                                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-xs text-zinc-300 outline-none"
                                >
                                    <option value="">— select snapshot —</option>
                                    {snapshots.map(s => (
                                        <option key={s.path} value={s.path}>
                                            {s.filename} ({s.count} vectors, {s.size_kb} KB)
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}

                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={merge}
                                onChange={e => setMerge(e.target.checked)}
                                className="rounded"
                            />
                            <span className="text-xs text-zinc-400">
                                Merge{" "}
                                <span className="text-zinc-600">(keep existing + add snapshot; uncheck to wipe first)</span>
                            </span>
                        </label>

                        {!merge && (
                            <div className="flex items-center gap-2 text-xs text-red-400 bg-red-950/30 border border-red-900/30 rounded-lg px-3 py-2">
                                <AlertCircle size={12} />
                                Merge is off — current index will be wiped before restore
                            </div>
                        )}

                        <button
                            onClick={handleImport}
                            disabled={importing || !importPath.trim()}
                            className="flex items-center gap-2 px-4 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
                        >
                            {importing
                                ? <><Loader2 size={14} className="animate-spin" /> Starting…</>
                                : <><Upload size={14} /> Restore Snapshot</>
                            }
                        </button>
                    </div>
                )}

                {/* ── DIFF ─────────────────────────────────────── */}
                {activeTab === "diff" && (
                    <div className="space-y-4">
                        <p className="text-xs text-zinc-500 leading-relaxed">
                            Compare two snapshots to see exactly what changed — useful for auditing
                            or computing the minimal delta to sync to cloud.
                        </p>

                        {["A (baseline)", "B (current)"].map((lbl, i) => (
                            <div key={i}>
                                <label className="text-xs text-zinc-500 uppercase tracking-wider mb-1.5 block">
                                    Snapshot {lbl}
                                </label>
                                <select
                                    value={i === 0 ? selectedA : selectedB}
                                    onChange={e => i === 0 ? setSelectedA(e.target.value) : setSelectedB(e.target.value)}
                                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-xs text-zinc-300 outline-none"
                                >
                                    <option value="">— select —</option>
                                    {snapshots.map(s => (
                                        <option key={s.path} value={s.path}>{s.filename}</option>
                                    ))}
                                </select>
                            </div>
                        ))}

                        <button
                            onClick={handleDiff}
                            disabled={diffLoading || !selectedA || !selectedB}
                            className="flex items-center gap-2 px-4 py-2 bg-violet-700 hover:bg-violet-600 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
                        >
                            {diffLoading
                                ? <><Loader2 size={14} className="animate-spin" /> Comparing…</>
                                : <><GitCompare size={14} /> Compare</>
                            }
                        </button>

                        {diffResult && (
                            <div className="space-y-3">
                                <p className="text-xs font-mono text-zinc-400 bg-zinc-800 px-3 py-2 rounded-lg">
                                    {diffResult.summary}
                                </p>
                                <div className="grid grid-cols-3 gap-2">
                                    {[
                                        { label: "Added", count: diffResult.added, color: "text-emerald-400", paths: diffResult.added_paths },
                                        { label: "Removed", count: diffResult.removed, color: "text-red-400", paths: diffResult.removed_paths },
                                        { label: "Changed", count: diffResult.changed, color: "text-amber-400", paths: diffResult.changed_paths },
                                    ].map(({ label, count, color, paths }) => (
                                        <div key={label} className="bg-zinc-800/60 border border-zinc-700/50 rounded-lg p-2">
                                            <div className={`text-lg font-bold ${color}`}>{count}</div>
                                            <div className="text-[10px] text-zinc-600">{label}</div>
                                            {paths?.slice(0, 3).map((p: string) => (
                                                <div key={p} className="text-[9px] text-zinc-700 truncate mt-0.5 font-mono">{p.split('/').pop()}</div>
                                            ))}
                                        </div>
                                    ))}
                                </div>
                                <p className="text-[10px] text-zinc-700">
                                    {diffResult.unchanged} files unchanged
                                </p>
                            </div>
                        )}
                    </div>
                )}
            </div>

            <Toast toasts={toasts} onDismiss={id => setToasts(t => t.filter(x => x.id !== id))} />
        </div>
    );
}