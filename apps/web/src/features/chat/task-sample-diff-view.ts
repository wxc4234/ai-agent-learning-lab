// 当前结果与历史共用校验，不能把独立Git仓库协议误认为应用样例。
export const SAMPLE_BASELINE_SHA256 = "01d09d19c2139a46aebfb577780d123d7396e97201bc7ead210a2ebff8239dee";
export type TaskSampleDiffView = {
    baselineSha256: string;
    contentSha256: string;
    diff: string;
    byteCount: number;
};
const fields = ["source", "status", "comparison", "path", "baseline_sha256", "content_sha256", "format", "encoding", "byte_count", "diff"];

export function parseTaskSampleDiff(toolName: string, raw: string): TaskSampleDiffView | null {
    if (toolName !== "read_task_sample_diff" || raw.length > 1024 * 1024) return null;
    try {
        const encoder = new TextEncoder();
        if (encoder.encode(raw).length > 1024 * 1024) return null;
        const value: unknown = JSON.parse(raw);
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        const item = value as Record<string, unknown>;
        if (Object.keys(item).length !== fields.length || !fields.every(key => Object.hasOwn(item, key))
            || item.source !== "task_application_sample" || item.status !== "complete"
            || item.comparison !== "fixed_old_to_snapshot" || item.path !== "example.txt"
            || item.baseline_sha256 !== SAMPLE_BASELINE_SHA256
            || typeof item.content_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(item.content_sha256)
            || item.format !== "git_diff" || item.encoding !== "utf-8"
            || typeof item.diff !== "string" || item.diff.length > 256 * 1024
            || typeof item.byte_count !== "number" || !Number.isSafeInteger(item.byte_count)
            || item.byte_count < 0 || item.byte_count > 256 * 1024) return null;
        const bytes = encoder.encode(item.diff);
        // 拒绝孤立代理项和虚假字节数；不替换或解释差异正文。
        if (bytes.length !== item.byte_count || new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes) !== item.diff) return null;
        return { baselineSha256: item.baseline_sha256, contentSha256: item.content_sha256, diff: item.diff, byteCount: item.byte_count };
    } catch {
        return null;
    }
}
