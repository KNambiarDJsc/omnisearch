import { useEffect, useRef } from "react";
import { SearchResult } from "../api";
import { ResultItem } from "./ResultItem";
import { Loader2 } from "lucide-react";

interface ResultsListProps {
    results: SearchResult[];
    selectedIndex: number;
    onSelectIndex: (index: number) => void;
    query: string;
    isLoading: boolean;
}

export function ResultsList({
    results,
    selectedIndex,
    onSelectIndex,
    query,
    isLoading,
}: ResultsListProps) {
    const selectedRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        selectedRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, [selectedIndex]);

    // Loading skeleton
    if (isLoading) {
        return (
            <div className="flex-1 overflow-hidden">
                <div className="px-4 pt-3 pb-1">
                    <div className="flex items-center gap-2 text-zinc-600">
                        <Loader2 size={12} className="animate-spin" />
                        <span className="text-[11px]">Searching…</span>
                    </div>
                </div>
                {[...Array(4)].map((_, i) => (
                    <SkeletonRow key={i} />
                ))}
            </div>
        );
    }

    // No results
    if (results.length === 0 && query.trim()) {
        return (
            <div className="flex flex-col items-center justify-center flex-1 py-12 text-zinc-600">
                <div className="text-2xl mb-3 opacity-40">◌</div>
                <p className="text-sm text-zinc-500">
                    No results for{" "}
                    <span className="text-zinc-400 font-medium">"{query}"</span>
                </p>
                <p className="text-xs mt-1.5 text-zinc-700">
                    Try different keywords, or index more folders via the settings icon
                </p>
            </div>
        );
    }

    // Empty state
    if (results.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center flex-1 py-12 text-zinc-700">
                <div className="grid grid-cols-2 gap-2 mb-5 opacity-60">
                    {EXAMPLE_QUERIES.map((q) => (
                        <ExampleChip key={q} label={q} />
                    ))}
                </div>
                <p className="text-xs text-zinc-600">Search using natural language</p>
            </div>
        );
    }

    return (
        <div className="flex-1 overflow-y-auto">
            {/* Result count header */}
            <div className="flex items-center justify-between px-4 py-1.5 sticky top-0 bg-zinc-900/80 backdrop-blur-sm z-10">
                <span className="text-[10px] text-zinc-600 uppercase tracking-wider font-medium">
                    {results.length} result{results.length !== 1 ? "s" : ""}
                </span>
                <span className="text-[10px] text-zinc-700">
                    ↑↓ to navigate · ↵ to open
                </span>
            </div>

            {results.map((result, index) => (
                <div
                    key={`${result.file_path}-${index}`}
                    ref={index === selectedIndex ? selectedRef : undefined}
                >
                    <ResultItem
                        result={result}
                        isSelected={index === selectedIndex}
                        onSelect={() => onSelectIndex(index)}
                    />
                </div>
            ))}

            <div className="h-2" /> {/* bottom padding */}
        </div>
    );
}

function SkeletonRow() {
    return (
        <div className="flex items-start gap-3 px-4 py-2.5 animate-pulse">
            <div className="w-4 h-4 bg-zinc-800 rounded mt-0.5 flex-shrink-0" />
            <div className="flex-1 space-y-1.5">
                <div className="flex gap-2">
                    <div className="h-3 bg-zinc-800 rounded w-40" />
                    <div className="h-3 bg-zinc-800 rounded w-10 ml-auto" />
                </div>
                <div className="h-2.5 bg-zinc-800 rounded w-full" />
                <div className="h-2.5 bg-zinc-800 rounded w-3/4" />
            </div>
        </div>
    );
}

function ExampleChip({ label }: { label: string }) {
    return (
        <div className="text-[11px] text-zinc-600 bg-zinc-800/50 border border-zinc-800 rounded-lg px-3 py-1.5 text-center">
            {label}
        </div>
    );
}

const EXAMPLE_QUERIES = [
    "invoice from last month",
    "python script using pandas",
    "meeting about budget",
    "architecture diagram",
];