import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

// 显式分类观察结果；eligible也不能自动获得写入语义。
export const assessmentLabels = {
    eligible: "当前基线与许可匹配，应用时仍会重新校验",
    invalid_facts: "检查依据不完整或不一致",
    not_authorized: "当前资源未获授权",
    apply_not_requested: "未提供拟应用意图",
    grant_missing: "未找到对应许可",
    grant_revoked: "许可已撤销",
    grant_changed: "许可修订已变化，请重新查询许可",
    target_changed: "目标文件或目录绑定已变化",
    proposal_not_approved: "提案尚未批准",
    application_not_idle: "应用机会已占用或已消耗",
    diff_incomplete: "提案差异不完整",
    baseline_changed: "原文件内容已变化",
    candidate_changed: "候选内容已变化",
    filesystem_unconfirmed: "无法确认文件系统状态",
    platform_unsupported: "当前平台不支持此检查",
    exclusive_access_unconfirmed: "无法确认文件的排他访问条件",
} as const;

export type AssessmentResult = keyof typeof assessmentLabels;

export function readAssessmentResult(
    raw: unknown, workspaceId: string, taskId: string, proposalId: string,
): AssessmentResult | null {
    if (![workspaceId, taskId, proposalId].every(isProposalIdentifier)
        || !raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const value = raw as Record<string, unknown>;
    if (value.workspace_id !== workspaceId || value.task_id !== taskId || value.proposal_id !== proposalId
        || typeof value.result !== "string" || !Object.hasOwn(assessmentLabels, value.result)) return null;
    return value.result as AssessmentResult;
}
