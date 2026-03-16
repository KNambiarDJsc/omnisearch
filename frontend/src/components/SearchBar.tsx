import { useRef, useEffect } from "react";
import { Search, Loader2 } from "lucide-react";

interface SearchBarProps {
    value: string;
    onChange: (value: string) => void;
    isLoading: boolean;
    placeholder?: string;
}

export function SearchBar({
    value,
    onChange,
    isLoading,
    placeholder = "Search your computer...",
}: SearchBarProps) {
    const inputRef = useRef<HTMLInputElement>(null);

    // Auto-focus on mount
    useEffect(() => {
        inputRef.current?.focus();
    }, []);

    return (
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-zinc-800">
            {isLoading ? (
                <Loader2 size={18} className="text-zinc-400 animate-spin flex-shrink-0" />
            ) : (
                <Search size={18} className="text-zinc-400 flex-shrink-0" />
            )}
            <input
                ref={inputRef}
                type="text"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                className="flex-1 bg-transparent text-zinc-100 text-[15px] placeholder-zinc-500 outline-none"
                spellCheck={false}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
            />
            {value && (
                <button
                    onClick={() => onChange("")}
                    className="text-zinc-500 hover:text-zinc-300 text-xs px-1.5 py-0.5 rounded border border-zinc-700 transition-colors"
                >
                    esc
                </button>
            )}
        </div>
    );
}