import { readFileEditProposalFields } from "../chat/file-edit-proposal-view.ts";

export type FileEditProposalDetail = {
    proposal_id: string;
    workspace_id: string;
    task_id: string;
    relative_path: string;
    status: "pending" | "approved" | "rejected";
    baseline_sha256: string;
    proposed_sha256: string;
    diff: string;
    diff_truncated: boolean;
    created_at: string;
};

export function isProposalIdentifier(value: string): boolean {
    // 显式长度检查，避免正则$对末尾换行的宽松匹配。
    return value.length === 32 && /^[0-9a-f]{32}$/.test(value);
}

export function readFileEditProposalDetail(
    raw: unknown,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): FileEditProposalDetail | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || !isProposalIdentifier(proposalId)
        || record.workspace_id !== workspaceId
        || record.task_id !== taskId
        || record.proposal_id !== proposalId
        || typeof record.diff !== "string"
        || record.diff.length === 0
        || record.diff.length > 32768
        || [...record.diff].length > 16384
    ) {
        return null;
    }

    const status = record.status;

    // 详情使用当前状态，未知状态不能默认为pending。
    if (
        status !== "pending"
        && status !== "approved"
        && status !== "rejected"
    ) {
        return null;
    }

    const fields = readFileEditProposalFields(record);
    if (fields === null) {
        return null;
    }

    // 与后端数据库约束一致，拒绝自相矛盾的上游响应。
    if (status === "approved" && fields.diffTruncated) {
        return null;
    }

    // 显式投影公开字段；不能直接转发原始上游对象。
    return {
        proposal_id: fields.proposalId,
        workspace_id: workspaceId,
        task_id: taskId,
        relative_path: fields.relativePath,
        status,
        baseline_sha256: fields.baselineSha256,
        proposed_sha256: fields.proposedSha256,
        diff: record.diff,
        diff_truncated: fields.diffTruncated,
        created_at: fields.createdAt,
    };
}
