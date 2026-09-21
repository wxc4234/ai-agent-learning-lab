// 创建回执只证明保存时为pending，不代表提案现在已获批准或文件已写入。
export type FileEditProposalView = {
    proposalId: string;
    relativePath: string;
    baselineSha256: string;
    proposedSha256: string;
    createdAt: string;
    diffTruncated: boolean;
};

function isHex(value: unknown, length: number): value is string {
    return typeof value === "string" && value.length === length && /^[0-9a-f]+$/.test(value);
}

function isTimestamp(value: unknown): value is string {
    if (typeof value !== "string" || value.length > 32) return false;
    // 要求显式时区；Date.parse会宽松接受错误日期，因此先校验日历和时分秒。
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)$/.exec(value);
    if (!match) return false;
    const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]
        && hour < 24 && minute < 60 && second < 60 && Number.isFinite(Date.parse(value));
}

export function parseFileEditProposal(toolName: string, raw: string): FileEditProposalView | null {
    if (toolName !== "create_file_edit_proposal") return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        const record = value as Record<string, unknown>;
        if (record.status !== "pending"
            || !isHex(record.proposal_id, 32)
            || typeof record.relative_path !== "string"
            || record.relative_path.length === 0 || record.relative_path.length > 8192
            || [...record.relative_path].length > 4096
            || !isHex(record.baseline_sha256, 64) || !isHex(record.proposed_sha256, 64)
            || !isTimestamp(record.created_at) || typeof record.diff_truncated !== "boolean") return null;
        // 显式复制公开字段，不传播服务端私有字段或未来新增内容。
        return {
            proposalId: record.proposal_id, relativePath: record.relative_path,
            baselineSha256: record.baseline_sha256, proposedSha256: record.proposed_sha256,
            createdAt: record.created_at, diffTruncated: record.diff_truncated,
        };
    } catch {
        return null;
    }
}
