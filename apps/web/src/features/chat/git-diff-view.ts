// 实时和历史共用公开协议校验；识别失败时回退原始文本，不伪装为空差异。
export type GitDiffView = {
    scope: "worktree" | "staged";
    diff: string;
    byteCount: number;
};

export function parseGitDiff(toolName: string, raw: string): GitDiffView | null {
    if (toolName !== "git_sample_diff" || raw.length > 1024 * 1024) return null;
    try {
        const encoder = new TextEncoder();
        if (encoder.encode(raw).length > 1024 * 1024) return null;
        const value: unknown = JSON.parse(raw);
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        const item = value as Record<string, unknown>;
        if (
            item.source !== "task_git_sample" || item.status !== "complete"
            || (item.scope !== "worktree" && item.scope !== "staged")
            || item.comparison !== (item.scope === "worktree" ? "index_to_worktree" : "head_to_index")
            || item.submodules !== "ignored" || item.untracked_files !== "excluded"
            || item.format !== "git_diff" || item.encoding !== "utf-8"
            || typeof item.diff !== "string" || item.diff.length > 256 * 1024
            || typeof item.byte_count !== "number" || !Number.isSafeInteger(item.byte_count)
            || item.byte_count < 0 || item.byte_count > 256 * 1024
        ) return null;
        const bytes = encoder.encode(item.diff);
        // 编码器会替换孤立代理项；往返核对避免悄悄展示被修改的内容。
        if (bytes.length !== item.byte_count || new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes) !== item.diff) return null;
        return { scope: item.scope, diff: item.diff, byteCount: item.byte_count };
    } catch {
        return null;
    }
}
