import { useState, useEffect } from "react";
import { FolderOpen, Plus, Trash2, RefreshCw, CheckCircle, AlertCircle } from "lucide-react";
import { indexFolder, watchFolder, unwatchFolder, getWatchedFolders } from "../api";

interface IndexPanelProps {
    onClose: () => void;
}

type ToastType = "success" | "error";

interface Toast {
    id: number;
    message: string;
    type: ToastType;
}

export function IndexPanel({ onClose }: IndexPanelProps) {
    const [folderPath, setFolderPath] = useState("");
    const [watchedFolders, setWatchedFolders] = useState<string[]>([]);
    const [isIndexing, setIsIndexing] = useState(false);
    const [toasts, setToasts] = useState<Toast[]>([]);

    useEffect(() => {
        loadWatched();
    }, []);

    async function loadWatched() {
        try {
            const { folders } = await getWatchedFolders();
            setWatchedFolders(folders);
        } catch { }
    }

    function addToast(message: string, type: ToastType) {
        const id = Date.now();
        setToasts((t) => [...t, { id, message, type }]);
        setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3000);
    }

    async function handleIndexAndWatch() {
        const path = folderPath.trim();
        if (!path) return;

        setIsIndexing(true);
        try {
            await indexFolder(path, true);
            await watchFolder(path);
            addToast(`Indexing started for "${path}"`, "success");
            setFolderPath("");
            await loadWatched();
        } catch (e: any) {
            addToast(e?.response?.data?.detail || "Failed to index folder", "error");
        } finally {
            setIsIndexing(false);
        }
    }

    async function handleUnwatch(path: string) {
        try {
            await unwatchFolder(path);
            setWatchedFolders((f) => f.filter((x) => x !== path));
            addToast("Removed from watch list", "success");
        } catch {
            addToast("Failed to unwatch folder", "error");
        }
    }

    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
                <div className="flex items-center gap-2">
                    <FolderOpen size={15} className="text-zinc-400" />
                    <span className="text-sm font-medium text-zinc-200">Index Folders</span>
                </div>
                <button
                    onClick={onClose}
                    className="text-xs text-zinc-500 hover:text-zinc-300 px-2 py-0.5 rounded border border-zinc-700 transition-colors"
                >
                    ← back
                </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {/* Add folder */}
                <div>
                    <label className="text-xs text-zinc-500 uppercase tracking-wider mb-2 block">
                        Add folder to index
                    </label>
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={folderPath}
                            onChange={(e) => setFolderPath(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleIndexAndWatch()}
                            placeholder="/Users/you/Documents"
                            className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-zinc-500 transition-colors font-mono"
                        />
                        <button
                            onClick={handleIndexAndWatch}
                            disabled={isIndexing || !folderPath.trim()}
                            className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors"
                        >
                            {isIndexing ? (
                                <RefreshCw size={13} className="animate-spin" />
                            ) : (
                                <Plus size={13} />
                            )}
                            {isIndexing ? "Indexing..." : "Index"}
                        </button>
                    </div>
                    <p className="text-[10px] text-zinc-600 mt-1.5">
                        Indexes all supported files and sets up live watching for new files.
                    </p>
                </div>

                {/* Watched folders */}
                {watchedFolders.length > 0 && (
                    <div>
                        <label className="text-xs text-zinc-500 uppercase tracking-wider mb-2 block">
                            Watched folders ({watchedFolders.length})
                        </label>
                        <div className="space-y-1.5">
                            {watchedFolders.map((folder) => (
                                <div
                                    key={folder}
                                    className="flex items-center justify-between gap-2 bg-zinc-800/50 rounded-lg px-3 py-2 border border-zinc-700/50"
                                >
                                    <div className="flex items-center gap-2 min-w-0">
                                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 flex-shrink-0" />
                                        <span className="text-xs font-mono text-zinc-300 truncate">{folder}</span>
                                    </div>
                                    <button
                                        onClick={() => handleUnwatch(folder)}
                                        className="text-zinc-600 hover:text-red-400 transition-colors flex-shrink-0"
                                    >
                                        <Trash2 size={13} />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {watchedFolders.length === 0 && (
                    <div className="text-center py-8 text-zinc-600">
                        <FolderOpen size={28} className="mx-auto mb-2 opacity-30" />
                        <p className="text-xs">No folders indexed yet.</p>
                        <p className="text-xs mt-0.5">Add a folder above to get started.</p>
                    </div>
                )}
            </div>

            {/* Toast notifications */}
            <div className="fixed bottom-4 right-4 space-y-2 z-50">
                {toasts.map((t) => (
                    <div
                        key={t.id}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg shadow-lg text-xs font-medium transition-all
              ${t.type === "success" ? "bg-emerald-900/90 text-emerald-300 border border-emerald-800" : "bg-red-900/90 text-red-300 border border-red-800"}
            `}
                    >
                        {t.type === "success" ? <CheckCircle size={13} /> : <AlertCircle size={13} />}
                        {t.message}
                    </div>
                ))}
            </div>
        </div>
    );
}