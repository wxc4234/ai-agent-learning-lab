// 实时与历史共用同一协议边界；来源字段不能代替当前任务范围校验。
export type VaultTaskScope = { workspaceId: string; taskId: string };
export type VaultIncompleteReason = "inventory_truncated" | "file_budget" | "match_budget";
export type VaultMatchView = {
    relativePath: string;
    sha256: string;
    lineNumber: number;
    columnNumber: number;
    snippet: string;
    snippetStartColumn: number;
    snippetTruncated: boolean;
};
export type VaultSearchView = {
    query: string;
    searchedFiles: number;
    readBytes: number;
    truncated: boolean;
    incompleteReasons: VaultIncompleteReason[];
    matches: VaultMatchView[];
};

export const VAULT_SEARCH_FAILURE = "检索失败，不能据此判断没有相关笔记。";
export const VAULT_INCOMPLETE_LABELS: Record<VaultIncompleteReason, string> = {
    inventory_truncated: "目录清单不完整，部分目录或文件未进入本次检索。",
    file_budget: "已达到 20 个文件的检索上限。",
    match_budget: "已达到 50 个命中行的返回上限。",
};

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const identifier = /^[0-9a-f]{32}$/;
const digest = /^[0-9a-f]{64}$/;
const reasons = new Set(Object.keys(VAULT_INCOMPLETE_LABELS));

function object(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, expected: readonly string[]): boolean {
    return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}
function integer(value: unknown, max = Number.MAX_SAFE_INTEGER): value is number {
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
}
function text(value: unknown, max: number): value is string {
    return typeof value === "string" && Array.from(value).length <= max
        && decoder.decode(encoder.encode(value)) === value;
}
function relativeMarkdownPath(value: unknown): value is string {
    if (!text(value, 4096) || value.length === 0 || !value.toLowerCase().endsWith(".md")) return false;
    // 只接受已规范化的非隐藏相对路径，不把文本变成 URL 或读取入口。
    return value.split("/").every(part => part.length > 0 && !part.startsWith(".")
        && !/[<>:"|?*\\\x00-\x1f]/u.test(part) && !/[ .]$/u.test(part)
        && !/^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/iu.test(part));
}

export function vaultSearchCoverage(value: VaultSearchView): string {
    if (value.matches.length === 0) {
        return value.truncated
            ? "当前已检索部分没有匹配，仍有内容未完整检查。"
            : "本次检索范围内没有匹配。";
    }
    return value.truncated
        ? "检索不完整，以下是本次已取得的匹配片段。"
        : `本次检索范围内找到 ${value.matches.length} 个命中行。`;
}

export function parseVaultSearch(
    toolName: string,
    raw: string,
    scope?: VaultTaskScope,
): VaultSearchView | null {
    // 先限制完整 UTF-8 JSON；缺失可信当前范围时不展示笔记内容。
    if (toolName !== "search_vault" || !scope
        || !identifier.test(scope.workspaceId) || !identifier.test(scope.taskId)
        || raw.length > 64 * 1024 || encoder.encode(raw).length > 64 * 1024) return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (!object(value) || !keys(value, [
            "source", "content_trust", "workspace_id", "task_id", "query", "matches",
            "searched_files", "read_bytes", "truncated", "incomplete_reasons",
        ]) || value.source !== "authorized_vault" || value.content_trust !== "untrusted"
            || value.workspace_id !== scope.workspaceId || value.task_id !== scope.taskId
            || !text(value.query, 128) || value.query.length === 0 || /[\r\n\x00]/u.test(value.query)
            || !integer(value.searched_files, 20) || !integer(value.read_bytes, 20 * 256 * 1024)
            || typeof value.truncated !== "boolean" || !Array.isArray(value.incomplete_reasons)
            || !value.incomplete_reasons.every(reason => typeof reason === "string" && reasons.has(reason))
            || new Set(value.incomplete_reasons).size !== value.incomplete_reasons.length
            || value.truncated !== (value.incomplete_reasons.length > 0)
            || !Array.isArray(value.matches) || value.matches.length > 50) return null;
        if ((value.searched_files === 0 && (value.read_bytes !== 0 || value.matches.length !== 0))
            || (value.incomplete_reasons.includes("file_budget") && value.searched_files !== 20)
            || (value.incomplete_reasons.includes("match_budget") && value.matches.length !== 50)) return null;

        const matches: VaultMatchView[] = [];
        const sources = new Map<string, string>();
        const lines = new Set<string>();
        for (const entry of value.matches) {
            if (!object(entry) || !keys(entry, [
                "source", "column_number", "snippet", "snippet_start_column", "snippet_truncated",
            ]) || !object(entry.source) || !keys(entry.source, [
                "workspace_id", "relative_path", "sha256", "start_line", "end_line",
            ])) return null;
            const source = entry.source;
            if (source.workspace_id !== scope.workspaceId || !relativeMarkdownPath(source.relative_path)
                || typeof source.sha256 !== "string" || !digest.test(source.sha256)
                || !integer(source.start_line) || source.start_line === 0 || source.end_line !== source.start_line
                || !integer(entry.column_number) || entry.column_number === 0
                || !integer(entry.snippet_start_column) || entry.snippet_start_column === 0
                || !text(entry.snippet, 200) || /[\r\n\x00]/u.test(entry.snippet)
                || typeof entry.snippet_truncated !== "boolean"
                || (!entry.snippet_truncated && entry.snippet_start_column !== 1)) return null;
            // 后端列号按 Unicode 码点计数，不能直接用 JS UTF-16 下标截取。
            const offset = entry.column_number - entry.snippet_start_column;
            const characters = Array.from(entry.snippet);
            if (offset < 0 || characters.slice(offset, offset + Array.from(value.query).length).join("") !== value.query) return null;
            const lineKey = `${source.relative_path}:${source.start_line}`;
            if (lines.has(lineKey) || (sources.has(source.relative_path) && sources.get(source.relative_path) !== source.sha256)) return null;
            lines.add(lineKey);
            sources.set(source.relative_path, source.sha256);
            matches.push({
                relativePath: source.relative_path,
                sha256: source.sha256,
                lineNumber: source.start_line,
                columnNumber: entry.column_number,
                snippet: entry.snippet,
                snippetStartColumn: entry.snippet_start_column,
                snippetTruncated: entry.snippet_truncated,
            });
        }
        if (sources.size > value.searched_files) return null;
        return {
            query: value.query,
            searchedFiles: value.searched_files,
            readBytes: value.read_bytes,
            truncated: value.truncated,
            incompleteReasons: value.incomplete_reasons as VaultIncompleteReason[],
            matches,
        };
    } catch {
        return null;
    }
}
