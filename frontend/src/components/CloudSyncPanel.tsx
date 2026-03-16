import { useState, useEffect } from "react";
import {
    Cloud, CloudOff, Upload, Download, RefreshCw,
    CheckCircle2, AlertCircle, Loader2, Clock,
    Shield, Database, ChevronDown, ChevronUp
} from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:5005";

interface SyncStatus {
    cloud_sync_enabled: boolean;
    last_sync?: {
        synced_at: number;
        direction: string;
        files_count: number;
        status: string;
        error: string;
    };
    r2_snapshots: Array<{
        key: string;
        filename: string;
        size_kb: number;
        last_modified: string;
    }>;
    r2_error?: string;
}

interface MetaStats {
    files: number;
    indexed: number;
    last_indexed: number;
    db_path: string;
}

type ToastType = "success" | "error" | "info";
interface Toast { id: number; message: string; type: ToastType; }

function formatTs(ts: number): string {
    if (!ts) return "never";
    return new Date(ts * 1000).toLocaleString();
}

function StatusDot({ ok }: { ok: boolean }) {
    return (
        <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${ok ? "bg-emerald-500" : "bg-zinc-600"}`} />
    );
}

export function CloudSyncPanel() {
    const [status, setStatus] = useState<SyncStatus | null>(null);
    const [metaStats, setMetaStats] = useState<MetaStats | null>(null);
    const [loading, setLoading] = useState(false);
    const [pushing, setPushing] = useState(false);
    const [pulling, setPulling] = useState(false);
    const [deltaOnly, setDeltaOnly] = useState(true);
    const [mergePull, setMergePull] = useState(true);
    const [showRemote, setShowRemote] = useState(false);
    const [toasts, setToasts] = useState<Toast[]>([]);

    useEffect(() => {
        loadStatus();
        loadMetaStats();
    }, []);

    function toast(message: string, type: ToastType = "info") {
        const id = Date.now();
        setToasts(t => [...t, { id, message, type }]);
        setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4000);
    }

    async function loadStatus() {
        setLoading(true);
        try {
            const res = await fetch(`${BASE_URL}/sync/status`);
            const data: SyncStatus = await res.json();
            setStatus(data);
        } catch { toast("Could not fetch sync status", "error"); }
        finally { setLoading(false); }
    }

    async function loadMetaStats() {
        try {
            const res = await fetch(`${BASE_URL}/metadata/stats`);
            setMetaStats(await res.json());
        } catch { }
    }

    async function handlePush() {
        if (!status?.cloud_sync_enabled) {
            toast("Enable cloud sync first — add R2 credentials to .env", "error");
            return;
        }
        setPushing(true);
        try {
            const res = await fetch(`${BASE_URL}/sync`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ delta_only: deltaOnly, include_metadata: true }),
            });
            const data = await res.json();
            if (data.status === "queued") {
                toast(`Push queued — ${deltaOnly ? "delta" : "full"} sync running in background`, "success");
                setTimeout(loadStatus, 3000);
            } else {
                toast("Push failed", "error");
            }
        } catch (e) { toast(`Push error: ${e}`, "error"); }
        finally { setPushing(false); }
    }

    async function handlePull(snapshotKey?: string) {
        if (!status?.cloud_sync_enabled) {
            toast("Enable cloud sync first", "error");
            return;
        }
        setPulling(true);
        try {
            const res = await fetch(`${BASE_URL}/sync/pull`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ merge: mergePull, snapshot_key: snapshotKey }),
            });
            const data = await res.json();
            if (data.status === "queued") {
                toast("Pull queued — restoring from R2 in background", "success");
                setTimeout(loadStatus, 3000);
            } else {
                toast("Pull failed", "error");
            }
        } catch (e) { toast(`Pull error: ${e}`, "error"); }
        finally { setPulling(false); }
    }

    const syncEnabled = status?.cloud_sync_enabled ?? false;
    const lastSync = status?.last_sync;
    const r2Snaps = status?.r2_snapshots ?? [];

    return (
        <div className="flex flex-col h-full min-h-0">

            {/* Header status bar */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 flex-shrink-0">
                <div className="flex items-center gap-2">
                    {syncEnabled
                        ? <Cloud size={14} className="text-blue-400" />
                        : <CloudOff size={14} className="text-zinc-600" />}
                    <span className="text-xs text-zinc-400">
                        {syncEnabled ? "R2 sync enabled" : "R2 sync disabled"}
                    </span>
                    <StatusDot ok={syncEnabled} />
                </div>
                <button
                    onClick={() => { loadStatus(); loadMetaStats(); }}
                    className="p-1.5 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors"
                >
                    <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
                </button>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 min-h-0 space-y-4">

                {/* Not configured notice */}
                {!syncEnabled && (
                    <div className="bg-zinc-800/40 border border-zinc-700/50 rounded-lg p-4 space-y-2">
                        <p className="text-xs text-zinc-300 font-medium">Cloud sync is off</p>
                        <p className="text-xs text-zinc-500 leading-relaxed">
                            To enable, add your Cloudflare R2 credentials to{" "}
                            <code className="text-zinc-400 bg-zinc-800 px-1 rounded">backend/.env</code>:
                        </p>
                        <div className="bg-zinc-900 rounded-lg p-3 font-mono text-[11px] text-zinc-400 space-y-0.5">
                            <div>R2_ACCOUNT_ID=your_account_id</div>
                            <div>R2_ACCESS_KEY=your_access_key</div>
                            <div>R2_SECRET_KEY=your_secret_key</div>
                            <div>R2_BUCKET_NAME=omnisearch</div>
                            <div className="text-emerald-500">ENABLE_CLOUD_SYNC=true</div>
                        </div>
                        <a
                            href="https://dash.cloudflare.com/?to=/:account/r2"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-block text-[11px] text-blue-400 hover:text-blue-300 transition-colors"
                        >
                            Get R2 credentials →
                        </a>
                    </div>
                )}

                {/* Local metadata stats */}
                {metaStats && (
                    <div>
                        <p className="text-[10px] text-zinc-600 uppercase tracking-wider mb-2">Local Index</p>
                        <div className="grid grid-cols-2 gap-2">
                            {[
                                { label: "Files in DB", value: metaStats.files.toLocaleString(), icon: <Database size={12} /> },
                                { label: "Indexed", value: metaStats.indexed.toLocaleString(), icon: <CheckCircle2 size={12} /> },
                            ].map(({ label, value, icon }) => (
                                <div key={label} className="bg-zinc-800/40 border border-zinc-700/40 rounded-lg p-3">
                                    <div className="flex items-center gap-1.5 text-zinc-500 mb-1">
                                        {icon}
                                        <span className="text-[10px] uppercase tracking-wider">{label}</span>
                                    </div>
                                    <div className="text-lg font-semibold text-zinc-200">{value}</div>
                                </div>
                            ))}
                        </div>
                        {metaStats.last_indexed && (
                            <p className="text-[10px] text-zinc-700 mt-1.5 flex items-center gap-1">
                                <Clock size={9} /> Last indexed: {formatTs(metaStats.last_indexed)}
                            </p>
                        )}
                    </div>
                )}

                {/* Last sync status */}
                {lastSync && (
                    <div className={`border rounded-lg p-3 ${lastSync.status === "ok"
                        ? "bg-emerald-950/30 border-emerald-900/40"
                        : "bg-red-950/30 border-red-900/40"
                        }`}>
                        <div className="flex items-center gap-2 mb-1">
                            {lastSync.status === "ok"
                                ? <CheckCircle2 size={12} className="text-emerald-400" />
                                : <AlertCircle size={12} className="text-red-400" />}
                            <span className="text-xs font-medium text-zinc-300">
                                Last sync: {lastSync.direction === "push" ? "↑ Pushed" : "↓ Pulled"}{" "}
                                {lastSync.files_count} files
                            </span>
                        </div>
                        <p className="text-[10px] text-zinc-600">{formatTs(lastSync.synced_at)}</p>
                        {lastSync.error && (
                            <p className="text-[10px] text-red-400 mt-1">{lastSync.error}</p>
                        )}
                    </div>
                )}

                {/* Encryption notice */}
                <div className="flex items-start gap-2 text-[11px] text-zinc-600 bg-zinc-800/20 border border-zinc-800 rounded-lg px-3 py-2">
                    <Shield size={11} className="flex-shrink-0 mt-0.5 text-zinc-500" />
                    <span>
                        All uploads are <strong className="text-zinc-400">AES-256-GCM encrypted</strong> before
                        leaving your machine. Only embeddings and metadata are synced — never your actual files.
                    </span>
                </div>

                {/* Push controls */}
                <div className="space-y-3">
                    <p className="text-[10px] text-zinc-600 uppercase tracking-wider">Backup to R2</p>

                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={deltaOnly}
                            onChange={e => setDeltaOnly(e.target.checked)}
                            className="rounded"
                        />
                        <span className="text-xs text-zinc-400">
                            Delta only{" "}
                            <span className="text-zinc-600">(faster — only upload what changed)</span>
                        </span>
                    </label>

                    <button
                        onClick={handlePush}
                        disabled={pushing}
                        className="flex items-center gap-2 w-full justify-center px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {pushing
                            ? <><Loader2 size={14} className="animate-spin" /> Pushing…</>
                            : <><Upload size={14} /> Backup Now</>
                        }
                    </button>
                </div>

                {/* Pull controls */}
                <div className="space-y-3">
                    <p className="text-[10px] text-zinc-600 uppercase tracking-wider">Restore from R2</p>

                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={mergePull}
                            onChange={e => setMergePull(e.target.checked)}
                            className="rounded"
                        />
                        <span className="text-xs text-zinc-400">
                            Merge{" "}
                            <span className="text-zinc-600">(keep existing — uncheck to wipe first)</span>
                        </span>
                    </label>

                    <button
                        onClick={() => handlePull()}
                        disabled={pulling || !syncEnabled}
                        className="flex items-center gap-2 w-full justify-center px-4 py-2.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {pulling
                            ? <><Loader2 size={14} className="animate-spin" /> Restoring…</>
                            : <><Download size={14} /> Restore Latest</>
                        }
                    </button>
                </div>

                {/* Remote snapshots list */}
                {syncEnabled && r2Snaps.length > 0 && (
                    <div>
                        <button
                            onClick={() => setShowRemote(v => !v)}
                            className="flex items-center gap-1.5 text-[10px] text-zinc-600 uppercase tracking-wider mb-2 hover:text-zinc-400 transition-colors"
                        >
                            {showRemote ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                            R2 Snapshots ({r2Snaps.length})
                        </button>

                        {showRemote && (
                            <div className="space-y-1.5">
                                {r2Snaps.map(snap => (
                                    <div
                                        key={snap.key}
                                        className="flex items-center justify-between gap-2 bg-zinc-800/40 border border-zinc-700/40 rounded-lg px-3 py-2"
                                    >
                                        <div className="min-w-0">
                                            <p className="text-[11px] font-mono text-zinc-300 truncate">{snap.filename}</p>
                                            <p className="text-[9px] text-zinc-600">
                                                {snap.size_kb} KB · {new Date(snap.last_modified).toLocaleString()}
                                            </p>
                                        </div>
                                        <button
                                            onClick={() => handlePull(snap.key)}
                                            disabled={pulling}
                                            className="text-[10px] text-zinc-500 hover:text-zinc-200 px-2 py-1 rounded border border-zinc-700 hover:border-zinc-500 transition-colors flex-shrink-0"
                                        >
                                            Restore
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {status?.r2_error && (
                    <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30 border border-red-900/30 rounded-lg px-3 py-2">
                        <AlertCircle size={12} className="flex-shrink-0 mt-0.5" />
                        R2 error: {status.r2_error}
                    </div>
                )}
            </div>

            {/* Toasts */}
            <div className="fixed bottom-4 right-4 space-y-2 z-50">
                {toasts.map(t => (
                    <div key={t.id} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium shadow-lg
            ${t.type === "success" ? "bg-emerald-900/90 text-emerald-300 border border-emerald-800"
                            : t.type === "error" ? "bg-red-900/90 text-red-300 border border-red-800"
                                : "bg-zinc-800 text-zinc-300 border border-zinc-700"}`}
                    >
                        {t.type === "success" && <CheckCircle2 size={12} />}
                        {t.type === "error" && <AlertCircle size={12} />}
                        {t.type === "info" && <Cloud size={12} />}
                        {t.message}
                    </div>
                ))}
            </div>
        </div>
    );
}