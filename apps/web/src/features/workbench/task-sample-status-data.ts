import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

type TaskSampleStatusScope = {
    workspace_id: string;
    task_id: string;
};

export type TaskSampleStatus = TaskSampleStatusScope & (
    | {
        status: "missing" | "busy" | "ready";
        sealed_reason: null;
    }
    | {
        status: "sealed";
        sealed_reason: "cleanup_pending" | "unavailable";
    }
);

export function readTaskSampleStatus(
    raw: unknown,
    workspaceId: string,
    taskId: string,
): TaskSampleStatus | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    // 标识必须格式正确且与本次请求一致，避免展示其他资源的状态。
    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || record.workspace_id !== workspaceId
        || record.task_id !== taskId
    ) {
        return null;
    }

    const status = record.status;

    // 未知值表示协议不符合要求，不能猜测为missing或ready。
    if (
        status !== "missing"
        && status !== "busy"
        && status !== "sealed"
        && status !== "ready"
    ) {
        return null;
    }

    const sealedReason = record.sealed_reason;

    // 原因仅用于解释封锁。缺失或不匹配的组合是协议错误，不能猜测状态。
    if (status === "sealed") {
        if (
            sealedReason !== "cleanup_pending"
            && sealedReason !== "unavailable"
        ) {
            return null;
        }

        return {
            workspace_id: workspaceId,
            task_id: taskId,
            status,
            sealed_reason: sealedReason,
        };
    }

    if (sealedReason !== null) {
        return null;
    }

    // 显式重建公开对象，丢弃目录、内部句柄和凭证等额外字段。
    // ready只是登记快照，不授予执行权限或解除提交保护。
    return {
        workspace_id: workspaceId,
        task_id: taskId,
        status,
        sealed_reason: null,
    };
}
