import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

export type ProposalApplicationStatus = {
    proposal_id: string;
    workspace_id: string;
    task_id: string;
    application_status: "idle" | "running" | "applied" | "not_applied" | "uncertain";
};

export function readProposalApplicationStatus(
    raw: unknown,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): ProposalApplicationStatus | null {
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
    ) {
        return null;
    }
    const status = record.application_status;
    // unknown是未确认回执，不是持久化状态；不能降级为idle。
    if (
        status !== "idle"
        && status !== "running"
        && status !== "applied"
        && status !== "not_applied"
        && status !== "uncertain"
    ) {
        return null;
    }
    // 显式投影，丢弃上游可能附带的令牌、目录、正文等私有字段。
    return {
        proposal_id: proposalId,
        workspace_id: workspaceId,
        task_id: taskId,
        application_status: status,
    };
}
