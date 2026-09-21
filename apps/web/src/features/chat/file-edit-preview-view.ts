// 校验公开结果后再展示；无法识别的协议交回通用文本组件。
export type FileEditPreviewView = {
    relativePath: string;
    baselineSha256: string;
    beforeByteCount: number;
    afterByteCount: number;
    diff: string;
    diffTruncated: boolean;
};

export type DiffLineKind = "metadata" | "added" | "removed" | "context";

const MAX_TEXT_BYTES = 256 * 1024;

function isBoundedText(
    value: unknown,
    maximum: number,
): value is string {
    // Python协议按Unicode字符计数，emoji可能占两个UTF-16单元。
    // 先检查长度，再展开计数，避免为明显超限文本分配数组。
    return (
        typeof value === "string"
        && value.length <= maximum * 2
        && [...value].length <= maximum
    );
}

function isByteCount(value: unknown): value is number {
    return (
        typeof value === "number"
        && Number.isSafeInteger(value)
        && value >= 0
        && value <= MAX_TEXT_BYTES
    );
}

export function parseFileEditPreview(
    toolName: string,
    raw: string,
): FileEditPreviewView | null {
    if (toolName !== "preview_file_edit") {
        return null;
    }

    try {
        const value: unknown = JSON.parse(raw);

        if (
            value === null
            || typeof value !== "object"
            || Array.isArray(value)
        ) {
            return null;
        }

        const record = value as Record<string, unknown>;

        if (
            record.status !== "preview_only"
            || !isBoundedText(record.relative_path, 4096)
            || record.relative_path.length === 0
            || typeof record.baseline_sha256 !== "string"
            || record.baseline_sha256.length !== 64
            || !/^[0-9a-f]{64}$/.test(record.baseline_sha256)
            || !isByteCount(record.before_byte_count)
            || !isByteCount(record.after_byte_count)
            || !isBoundedText(record.diff, 16384)
            || record.diff.length === 0
            || typeof record.diff_truncated !== "boolean"
        ) {
            return null;
        }

        // 只复制展示字段，不传播未来扩展字段或完整修改后内容。
        // 这里做协议校验，不替代服务端路径授权或文件基线检查。
        return {
            relativePath: record.relative_path,
            baselineSha256: record.baseline_sha256,
            beforeByteCount: record.before_byte_count,
            afterByteCount: record.after_byte_count,
            diff: record.diff,
            diffTruncated: record.diff_truncated,
        };
    } catch {
        return null;
    }
}

export function classifyDiffLine(line: string): DiffLineKind {
    // 文件头也以加减号开头，必须先于普通增删行判断。
    if (
        line.startsWith("--- ")
        || line.startsWith("+++ ")
        || line.startsWith("@@")
    ) {
        return "metadata";
    }

    if (line.startsWith("+")) {
        return "added";
    }

    if (line.startsWith("-")) {
        return "removed";
    }

    return "context";
}
