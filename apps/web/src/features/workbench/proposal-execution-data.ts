import { isProposalIdentifier } from "./file-edit-proposal-data.ts";

export type ProposalExecutionFileStatus =
    | "not_attempted"
    | "not_replaced"
    | "replaced"
    | "uncertain";

export type ProposalExecutionApplicationStatus =
    | "applied"
    | "not_applied"
    | "uncertain"
    | "unknown";

export type ProposalExecutionCode =
    | "proposal_application_applied"
    | "proposal_application_not_applied"
    | "proposal_application_uncertain"
    | "proposal_application_claim_unconfirmed"
    | "proposal_application_registration_unconfirmed";

export type ProposalExecutionReceipt = {
    proposal_id: string;
    workspace_id: string;
    task_id: string;
    file_status: ProposalExecutionFileStatus;
    application_status: ProposalExecutionApplicationStatus;
    code: ProposalExecutionCode;
    cleanup_complete: boolean | null;
};

type ConfirmedOutcome = Exclude<
    ProposalExecutionApplicationStatus,
    "unknown"
>;

const CONFIRMED_CODES: Record<
    ConfirmedOutcome,
    ProposalExecutionCode
> = {
    applied: "proposal_application_applied",
    not_applied: "proposal_application_not_applied",
    uncertain: "proposal_application_uncertain",
};

function readExpectedOutcome(
    fileStatus: ProposalExecutionFileStatus,
    cleanupComplete: boolean | null,
): ConfirmedOutcome | null {
    // 尚未进入文件原语时，没有清理回执，不能虚构 true 或 false。
    if (fileStatus === "not_attempted") {
        return cleanupComplete === null ? "not_applied" : null;
    }

    // 文件原语异常退出时，可能没有取得清理结果。
    if (fileStatus === "uncertain") {
        return "uncertain";
    }

    // replaced / not_replaced 是正常返回的文件回执，必须有清理结果。
    if (cleanupComplete === null) {
        return null;
    }

    // 文件结果明确但清理失败，仍不能确认整个应用操作成功结束。
    if (!cleanupComplete) {
        return "uncertain";
    }

    return fileStatus === "replaced" ? "applied" : "not_applied";
}

export function readProposalExecutionReceipt(
    raw: unknown,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): ProposalExecutionReceipt | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
    }

    const record = raw as Record<string, unknown>;

    // 必须有全部公开字段；不从原型链或默认值补出一份回执。
    const requiredFields = [
        "proposal_id",
        "workspace_id",
        "task_id",
        "file_status",
        "application_status",
        "code",
        "cleanup_complete",
    ];

    if (!requiredFields.every((field) => Object.hasOwn(record, field))) {
        return null;
    }

    // 核对当前操作范围，不能把其他任务的合法响应展示到当前任务。
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

    const fileStatus = record.file_status;
    const applicationStatus = record.application_status;
    const code = record.code;
    const cleanupComplete = record.cleanup_complete;

    if (
        fileStatus !== "not_attempted"
        && fileStatus !== "not_replaced"
        && fileStatus !== "replaced"
        && fileStatus !== "uncertain"
    ) {
        return null;
    }

    // idle / running 属于状态查询协议，不属于执行回执的确认结果。
    if (
        applicationStatus !== "applied"
        && applicationStatus !== "not_applied"
        && applicationStatus !== "uncertain"
        && applicationStatus !== "unknown"
    ) {
        return null;
    }

    if (
        code !== "proposal_application_applied"
        && code !== "proposal_application_not_applied"
        && code !== "proposal_application_uncertain"
        && code !== "proposal_application_claim_unconfirmed"
        && code !== "proposal_application_registration_unconfirmed"
    ) {
        return null;
    }

    // 不接受 0、1、"true" 等隐式转换；缺字段也不能解释成 null。
    if (
        cleanupComplete !== null
        && typeof cleanupComplete !== "boolean"
    ) {
        return null;
    }

    const expectedOutcome = readExpectedOutcome(
        fileStatus,
        cleanupComplete,
    );

    if (expectedOutcome === null) {
        return null;
    }

    if (applicationStatus === "unknown") {
        if (code === "proposal_application_claim_unconfirmed") {
            // 领取未确认时不能已经进入文件替换阶段。
            if (
                fileStatus !== "not_attempted"
                || cleanupComplete !== null
            ) {
                return null;
            }
        } else if (
            code !== "proposal_application_registration_unconfirmed"
        ) {
            return null;
        }

        // 登记未确认时保留文件证据，不能猜测数据库实际状态。
    } else if (
        applicationStatus !== expectedOutcome
        || code !== CONFIRMED_CODES[expectedOutcome]
    ) {
        return null;
    }

    // 显式创建公开对象，丢弃上游可能附带的令牌、路径和正文。
    // 这里不修改输入，也不发起查询、执行或重试。
    return {
        proposal_id: proposalId,
        workspace_id: workspaceId,
        task_id: taskId,
        file_status: fileStatus,
        application_status: applicationStatus,
        code,
        cleanup_complete: cleanupComplete,
    };
}
