


import { SearchResult } from "../api";

interface FooterProps {
    results: SearchResult[];
    selectedIndex: number;
    backendOnline: boolean;
    indexedCount: number;
}

export function Footer({ results, selectedIndex, backendOnline, indexedCount }: FooterProps) {
    return (
        <div className="flex items-center justify-between px-4 py-2 border-t border-zinc-800 text-[10px] text-zinc-500">
            {/* Left — keyboard hints */}
            <div className="flex items-center gap-3">
                <Hint keys={["↑", "↓"]} label="navigate" />
                <Hint keys={["↵"]} label="open" />
                <Hint keys={["⌘", "C"]} label="copy path" />
            </div>

            {/* Right — status */}
            <div className="flex items-center gap-3">
                {results.length > 0 && (
                    <span className="text-zinc-500">
                        {selectedIndex + 1} <span className="text-zinc-700">/</span> {results.length}
                    </span>
                )}
                {indexedCount > 0 && (
                    <span className="text-zinc-600 border-l border-zinc-800 pl-3">
                        {indexedCount.toLocaleString()} indexed
                    </span>
                )}
                <span className={`flex items-center gap-1.5 ${backendOnline ? "text-emerald-600" : "text-red-600"}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${backendOnline ? "bg-emerald-500" : "bg-red-500"}`} />
                    {backendOnline ? "ready" : "offline"}
                </span>
            </div>
        </div>
    );
}

function Hint({ keys, label }: { keys: string[]; label: string }) {
    return (
        <span className="flex items-center gap-1">
            {keys.map((k) => (
                <kbd
                    key={k}
                    className="px-1 py-0.5 rounded bg-zinc-800 border border-zinc-700 font-mono text-[9px] text-zinc-400"
                >
                    {k}
                </kbd>
            ))}
            <span className="text-zinc-600 ml-0.5">{label}</span>
        </span>
    );
}