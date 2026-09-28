import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

export type GrantRecord = { grant_id: string; revision: 1 | 2; status: "enabled" | "revoked" };
export type GrantReceipt = { grant: GrantRecord | null };

export function readGrantReceipt(raw: unknown, workspace: string, task: string, proposal: string): GrantReceipt | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const value = raw as Record<string, unknown>;
    if (value.workspace_id !== workspace || value.task_id !== task || value.proposal_id !== proposal) return null;
    if (value.grant === null) return { grant: null };
    if (!value.grant || typeof value.grant !== "object" || Array.isArray(value.grant)) return null;
    const grant = value.grant as Record<string, unknown>;
    if (typeof grant.grant_id !== "string" || !isProposalIdentifier(grant.grant_id)) return null;
    if ((grant.revision === 1 && grant.status === "enabled") || (grant.revision === 2 && grant.status === "revoked")) {
        return { grant: { grant_id: grant.grant_id, revision: grant.revision, status: grant.status } };
    }
    return null;
}

// 未知写请求不能仅凭一次“无记录/仍启用”查询证明已经停止。
export function grantChangeObserved(marker: string | null, grant: GrantRecord | null): boolean {
    return marker === null || (marker === "issue" && grant !== null)
        || (marker === `revoke:${grant?.grant_id}` && grant?.status === "revoked");
}
