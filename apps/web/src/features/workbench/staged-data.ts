// 同源代理和客户端共用的公开契约；不把任意后端字段传给UI。
export type StagedVersion = { mode: number; object_id: string };
export type StagedItem = {
    path: string; status: "added" | "deleted" | "modified" | "unmerged";
    head: StagedVersion | null;
    index: { stage: number; version: StagedVersion }[];
};
export type StagedData = {
    workspace_id: string; task_id: string; binding_revision: number; scope: "staged_only";
    status: "staged_compared" | "comparison_unavailable";
    unavailable_reasons: string[]; changes: StagedItem[] | null;
};
export const MAX_STAGED_BYTES = 1024 * 1024;
const modes = new Set([0o100644, 0o100755, 0o120000, 0o160000]);
const reasons = new Set(["no_head_object_observed", "commit_object_missing", "index_missing"]);
function record(v: unknown): v is Record<string, unknown> {
    return !!v && typeof v === "object" && !Array.isArray(v);
}
function exact(v: Record<string, unknown>, keys: string[]): boolean {
    return Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
}
function version(v: unknown): v is StagedVersion {
    return record(v) && exact(v, ["mode", "object_id"]) && typeof v.mode === "number" && modes.has(v.mode)
        && typeof v.object_id === "string" && /^[0-9a-f]{40}$/.test(v.object_id) && !/^0+$/.test(v.object_id);
}
export function readStagedData(raw: unknown, workspaceId: string, taskId: string, revision: number): StagedData | null {
    if (![workspaceId, taskId].every(v => /^[0-9a-f]{32}$/.test(v)) || !record(raw) || !exact(raw, ["workspace_id", "task_id", "binding_revision", "scope", "status", "unavailable_reasons", "changes"])
        || raw.workspace_id !== workspaceId || raw.task_id !== taskId || raw.binding_revision !== revision
        || !Number.isSafeInteger(revision) || revision < 1 || raw.scope !== "staged_only"
        || !Array.isArray(raw.unavailable_reasons) || raw.unavailable_reasons.some(v => typeof v !== "string" || !reasons.has(v))
        || new Set(raw.unavailable_reasons).size !== raw.unavailable_reasons.length) return null;
    if (raw.status === "comparison_unavailable") {
        if (raw.changes !== null || !raw.unavailable_reasons.length) return null;
    } else if (raw.status === "staged_compared") {
        if (raw.unavailable_reasons.length || !Array.isArray(raw.changes) || raw.changes.length > 4000) return null;
        const seen = new Set<string>();
        for (const item of raw.changes) {
            if (!record(item) || !exact(item, ["path", "status", "head", "index"])
                || typeof item.path !== "string" || !item.path || new TextEncoder().encode(item.path).length > 4096
                || /[\\:\u0000-\u001f\u007f]/.test(item.path)
                || item.path.split("/").some(p => !p || p === "." || p === ".." || p.toLowerCase() === ".git")
                || Array.from(item.path).some(c => { const point = c.codePointAt(0)!; return point >= 0xd800 && point <= 0xdfff; })
                || seen.has(item.path) || (item.head !== null && !version(item.head))
                || !Array.isArray(item.index) || item.index.length > 3) return null;
            seen.add(item.path);
            let last = -1;
            for (const v of item.index) {
                if (!record(v) || !exact(v, ["stage", "version"]) || !Number.isInteger(v.stage)
                    || typeof v.stage !== "number" || v.stage < 0 || v.stage > 3 || v.stage <= last || !version(v.version)) return null;
                last = v.stage;
            }
            const normal = item.index.length === 1 && item.index[0].stage === 0;
            if (item.status === "added") { if (item.head !== null || !normal) return null; }
            else if (item.status === "deleted") { if (item.head === null || item.index.length) return null; }
            else if (item.status === "modified") {
                if (item.head === null || !normal || (item.head.mode === item.index[0].version.mode
                    && item.head.object_id === item.index[0].version.object_id)) return null;
            } else if (item.status === "unmerged") { if (!item.index.length || item.index[0].stage === 0) return null; }
            else return null;
        }
    } else return null;
    return raw as unknown as StagedData;
}
