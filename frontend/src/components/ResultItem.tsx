import React from "react";
import { ExternalLink, Copy, FolderOpen, FileText, Code, Image, Music, Video, File } from "lucide-react";
import { SearchResult, openFile, copyToClipboard } from "../api";

interface ResultItemProps {
    result: SearchResult;
    isSelected: boolean;
    onSelect: () => void;
}

// Icon per category
function FileIcon({ fileType }: { fileType: string }) {
    const t = fileType.toLowerCase();
    const cls = "flex-shrink-0 mt-0.5";
    const sz = 15;

    if (["pdf", "docx", "doc", "txt", "md", "html", "csv"].includes(t))
        return <FileText size={sz} className={`${cls} text-blue-400`} />;
    if (["py", "js", "ts", "jsx", "tsx", "json", "yaml", "yml", "rs", "go", "java", "sh"].includes(t))
        return <Code size={sz} className={`${cls} text-emerald-400`} />;
    if (["png", "jpg", "jpeg", "webp", "gif"].includes(t))
        return <Image size={sz} className={`${cls} text-purple-400`} />;
    if (["mp3", "wav", "m4a", "ogg"].includes(t))
        return <Music size={sz} className={`${cls} text-orange-400`} />;
    if (["mp4", "mov", "avi", "mkv"].includes(t))
        return <Video size={sz} className={`${cls} text-pink-400`} />;
    return <File size={sz} className={`${cls} text-zinc-400`} />;
}

function CategoryBadge({ fileType }: { fileType: string }) {
    const t = fileType.toLowerCase();
    let label = t.toUpperCase();
    let color = "bg-zinc-800 text-zinc-500";

    if (["pdf", "docx", "doc", "txt", "md"].includes(t)) color = "bg-blue-950 text-blue-400";
    else if (["py", "js", "ts", "jsx", "tsx", "json"].includes(t)) color = "bg-emerald-950 text-emerald-400";
    else if (["png", "jpg", "jpeg", "webp"].includes(t)) color = "bg-purple-950 text-purple-400";
    else if (["mp3", "wav"].includes(t)) color = "bg-orange-950 text-orange-400";
    else if (["mp4", "mov"].includes(t)) color = "bg-pink-950 text-pink-400";

    return (
        <span className={`text-[9px] font-mono font-semibold px-1.5 py-0.5 rounded ${color}`}>
            {label}
        </span>
    );
}

function ScoreBar({ score }: { score: number }) {
    const pct = Math.round(score * 100);
    const color = score > 0.75 ? "bg-emerald-500" : score > 0.5 ? "bg-blue-500" : "bg-zinc-600";
    return (
        <div className="flex items-center gap-1.5 flex-shrink-0">
            <div className="w-12 h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[10px] text-zinc-600 tabular-nums w-7 text-right">{pct}%</span>
        </div>
    );
}

export function ResultItem({ result, isSelected, onSelect }: ResultItemProps) {
    const handleOpen = (e: React.MouseEvent) => {
        e.stopPropagation();
        openFile(result.file_path);
    };
    const handleCopy = (e: React.MouseEvent) => {
        e.stopPropagation();
        copyToClipboard(result.file_path);
    };
    const handleFolder = (e: React.MouseEvent) => {
        e.stopPropagation();
        const folder = result.file_path.substring(0, result.file_path.lastIndexOf("/"));
        openFile(folder);
    };

    // Highlight matched terms in snippet (naive, good enough for MVP)
    const snippetText = result.snippet || "No preview available";

    return (
        <div
            onClick={onSelect}
            className={`
        group flex items-start gap-3 px-4 py-2.5 cursor-pointer select-none transition-colors
        border-l-2
        ${isSelected
                    ? "bg-zinc-800/80 border-blue-500"
                    : "border-transparent hover:bg-zinc-800/40 hover:border-zinc-700"}
      `}
        >
            {/* File icon */}
            <div className="mt-0.5">
                <FileIcon fileType={result.file_type} />
            </div>

            {/* Main content */}
            <div className="flex-1 min-w-0">
                {/* Top row: filename + badge + score */}
                <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[13px] font-medium text-zinc-100 truncate flex-1">
                        {result.filename}
                    </span>
                    <CategoryBadge fileType={result.file_type} />
                    <ScoreBar score={result.score} />
                </div>

                {/* Snippet */}
                <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
                    {snippetText}
                </p>

                {/* Path — only show on selected */}
                {isSelected && (
                    <p className="text-[10px] text-zinc-700 mt-1 truncate font-mono">
                        {result.file_path}
                    </p>
                )}
            </div>

            {/* Action buttons */}
            <div className={`
        flex items-center gap-0.5 flex-shrink-0 self-center
        transition-opacity duration-100
        ${isSelected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}
      `}>
                <Btn icon={<ExternalLink size={12} />} label="Open file" onClick={handleOpen} />
                <Btn icon={<FolderOpen size={12} />} label="Open folder" onClick={handleFolder} />
                <Btn icon={<Copy size={12} />} label="Copy path" onClick={handleCopy} />
            </div>
        </div>
    );
}

function Btn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: (e: React.MouseEvent) => void }) {
    return (
        <button
            onClick={onClick}
            title={label}
            className="p-1.5 rounded text-zinc-600 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
        >
            {icon}
        </button>
    );
}