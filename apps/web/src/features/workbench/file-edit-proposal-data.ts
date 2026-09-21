import { parseFileEditProposal } from "../chat/file-edit-proposal-view.ts";

export type FileEditProposalDetail = {
    proposal_id: string;
    workspace_id: string;
    task_id: string;
    relative_path: string;
    status: "pending";
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
    if (
        !raw
        || typeof raw !== "object"
        || Array.isArray(raw)
    ) {
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

    try {
        // 复用回执解析器对共有字段的校验，包括状态、路径、
        // 双摘要和带时区日期；这里不会执行任何模型工具。
        const receipt = parseFileEditProposal(
            "create_file_edit_proposal",
            JSON.stringify(raw),
        );

        if (receipt === null) {
            return null;
        }

        // 显式构造公开响应，不能直接返回raw或展开整个record。
        return {
            proposal_id: receipt.proposalId,
            workspace_id: workspaceId,
            task_id: taskId,
            relative_path: receipt.relativePath,
            status: "pending",
            baseline_sha256: receipt.baselineSha256,
            proposed_sha256: receipt.proposedSha256,
            diff: record.diff,
            diff_truncated: receipt.diffTruncated,
            created_at: receipt.createdAt,
        };
    } catch {
        return null;
    }
}
