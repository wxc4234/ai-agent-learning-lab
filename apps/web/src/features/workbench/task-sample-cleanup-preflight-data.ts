import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

export type TaskSampleCleanupPreflightResult =
    | "evidence_missing"
    | "not_pending"
    | "evidence_inconsistent"
    | "directory_missing"
    | "identity_unverifiable"
    | "identity_matches_record"
    | "inspection_unavailable";

export type TaskSampleCleanupPreflight = {
    workspace_id: string;
    task_id: string;
    result: TaskSampleCleanupPreflightResult;
};

export function readTaskSampleCleanupPreflight(
    raw: unknown,
    workspaceId: string,
    taskId: string,
): TaskSampleCleanupPreflight | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    // 标识仅定位本次请求资源；不接受上游返回其他 Task 的诊断。
    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || !Object.hasOwn(record, "workspace_id")
        || !Object.hasOwn(record, "task_id")
        || !Object.hasOwn(record, "result")
        || record.workspace_id !== workspaceId
        || record.task_id !== taskId
    ) {
        return null;
    }

    const result = record.result;

    // 未知分类不能猜测为目录缺失、身份匹配或可清理。
    if (
        result !== "evidence_missing"
        && result !== "not_pending"
        && result !== "evidence_inconsistent"
        && result !== "directory_missing"
        && result !== "identity_unverifiable"
        && result !== "identity_matches_record"
        && result !== "inspection_unavailable"
    ) {
        return null;
    }

    // 仅投影公开分类和资源标识，绝不透传路径、身份数值或句柄。
    return {
        workspace_id: workspaceId,
        task_id: taskId,
        result,
    };
}
