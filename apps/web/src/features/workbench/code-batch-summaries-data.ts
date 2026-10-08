import { isCodeContextIdentifier } from "./code-query-context-data.ts";

// 只描述已保存的选择快照；空间ID/覆盖声明不授予后续访问或发送权。
export type CodeBatchSummary = {
    batch_id: string; space_id: string; requested_model: string; response_model: string;
    dimensions: number; chunk_count: number; truncated: boolean;
    incomplete_reasons: string[]; created_at: string;
};
export type CodeBatchSummaries = {
    workspace_id: string; task_id: string; batches: CodeBatchSummary[];
    has_more: boolean; limit: 20; source: "code_embedding_batch_summaries";
};

const encoder = new TextEncoder();
const whitespace = new RegExp("^[\\p{White_Space}\\u001c-\\u001f]+|[\\p{White_Space}\\u001c-\\u001f]+$", "gu");
const coverage = ["directory_entries", "directory_budget", "depth_budget", "file_budget", "unsupported_path", "chunk_budget"];

function exact(raw: unknown, fields: string): raw is Record<string, unknown> {
    const keys = fields.split(" ");
    return raw !== null && typeof raw === "object" && !Array.isArray(raw)
        && Object.keys(raw).length === keys.length && keys.every(key => Object.hasOwn(raw, key));
}
function integer(value: unknown, minimum: number, maximum: number): value is number {
    return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
function label(value: unknown): value is string {
    return typeof value === "string" && value.length > 0 && [...value].length <= 256
        && encoder.encode(value).length <= 1024 && value === value.replace(whitespace, "")
        && !/[\u0000-\u001f\u007f]/.test(value)
        && [...value].every(char => { const code = char.codePointAt(0)!; return code < 0xd800 || code > 0xdfff; });
}

function timestamp(value: unknown): bigint | null {
    if (typeof value !== "string") return null;
    // 当前Python/PG公开时间精确到微秒；Date.parse会丢精度且可能修正坏日期。
    const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
    if (!parts || parts[0] !== value) return null;
    const [, y, m, d, h, minute, second, fraction = "", zone, sign, zh = "0", zm = "0"] = parts;
    const [year, month, day, hour, minutes, seconds, zoneHour, zoneMinute] = [y, m, d, h, minute, second, zh, zm].map(Number);
    if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23
        || minutes > 59 || seconds > 59 || zoneHour > 23 || zoneMinute > 59) return null;
    const date = new Date(0);
    date.setUTCFullYear(year, month - 1, day);
    date.setUTCHours(hour, minutes, seconds, 0);
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
    const offset = zone === "Z" ? 0 : (zoneHour * 60 + zoneMinute) * (sign === "-" ? -1 : 1);
    // 先在安全毫秒整数上转BigInt，再补微秒/时区，避免年份较大时数值舍入。
    return BigInt(date.getTime()) * BigInt(1000) + BigInt(fraction.padEnd(6, "0")) - BigInt(offset) * BigInt(60_000_000);
}

export function readCodeBatchSummaries(raw: unknown, workspaceId: string, taskId: string): CodeBatchSummaries | null {
    if (![workspaceId, taskId].every(isCodeContextIdentifier)
        || !exact(raw, "workspace_id task_id batches has_more limit source")
        || raw.workspace_id !== workspaceId || raw.task_id !== taskId || raw.limit !== 20
        || raw.source !== "code_embedding_batch_summaries" || typeof raw.has_more !== "boolean"
        || !Array.isArray(raw.batches) || raw.batches.length > 20 || (raw.has_more && raw.batches.length !== 20)) return null;
    const batches: CodeBatchSummary[] = [];
    const seen = new Set<string>();
    const spaces = new Map<string, { dimensions: number; requested_model: string; response_model: string }>();
    let previousTime: bigint | null = null;
    let previousId = "";
    for (const item of raw.batches) {
        if (!exact(item, "batch_id space_id requested_model response_model dimensions chunk_count truncated incomplete_reasons created_at")
            || !isCodeContextIdentifier(item.batch_id) || seen.has(item.batch_id)
            || typeof item.space_id !== "string" || item.space_id.length !== 64 || !/^[a-f0-9]+$/.test(item.space_id)
            || !label(item.requested_model) || !label(item.response_model)
            || !integer(item.dimensions, 1, 4096) || !integer(item.chunk_count, 1, 20) || typeof item.truncated !== "boolean"
            || !Array.isArray(item.incomplete_reasons) || item.incomplete_reasons.length > 6
            || !item.incomplete_reasons.every(reason => typeof reason === "string" && coverage.includes(reason))
            || new Set(item.incomplete_reasons).size !== item.incomplete_reasons.length
            || item.truncated !== (item.incomplete_reasons.length > 0)) return null;
        const time = timestamp(item.created_at);
        if (time === null || (previousTime !== null && (time > previousTime || (time === previousTime && item.batch_id > previousId)))) return null;
        const space = spaces.get(item.space_id);
        if (space && (space.dimensions !== item.dimensions || space.requested_model !== item.requested_model || space.response_model !== item.response_model)) return null;
        spaces.set(item.space_id, { dimensions: item.dimensions, requested_model: item.requested_model, response_model: item.response_model });
        previousTime = time; previousId = item.batch_id; seen.add(item.batch_id);
        // 显式重建字段与原因数组；UI修改不会改写上游快照，也不复制私有来源。
        batches.push({
            batch_id: item.batch_id, space_id: item.space_id,
            requested_model: item.requested_model, response_model: item.response_model,
            dimensions: item.dimensions, chunk_count: item.chunk_count, truncated: item.truncated,
            incomplete_reasons: [...item.incomplete_reasons], created_at: item.created_at as string,
        });
    }
    return { workspace_id: workspaceId, task_id: taskId, batches, has_more: raw.has_more, limit: 20, source: "code_embedding_batch_summaries" };
}
