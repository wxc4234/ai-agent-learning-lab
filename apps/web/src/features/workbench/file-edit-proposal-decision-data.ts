import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

export type FileEditProposalDecision = "approved" | "rejected";

export type FileEditProposalDecisionReceipt = {
    proposal_id: string;
    workspace_id: string;
    task_id: string;
    status: FileEditProposalDecision;
};

export function readFileEditProposalDecision(
    raw: unknown,
): FileEditProposalDecision | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    // 正文只允许一个decision字段，不能夹带身份、正文或其他资源标识。
    if (
        Object.keys(record).length !== 1
        || !Object.hasOwn(record, "decision")
        || (
            record.decision !== "approved"
            && record.decision !== "rejected"
        )
    ) {
        return null;
    }

    return record.decision;
}

export function readFileEditProposalDecisionReceipt(
    raw: unknown,
    workspaceId: string,
    taskId: string,
    proposalId: string,
    decision: FileEditProposalDecision,
): FileEditProposalDecisionReceipt | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    // 200与合法JSON仍不足以证明响应属于本次操作。
    // 三个标识及最终状态必须与请求完全一致。
    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || !isProposalIdentifier(proposalId)
        || record.workspace_id !== workspaceId
        || record.task_id !== taskId
        || record.proposal_id !== proposalId
        || record.status !== decision
    ) {
        return null;
    }

    // 只输出公开字段，丢弃上游可能附带的内部数据。
    return {
        proposal_id: proposalId,
        workspace_id: workspaceId,
        task_id: taskId,
        status: decision,
    };
}
