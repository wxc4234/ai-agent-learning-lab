import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";
import {
    readFileEditProposalDecision,
    readFileEditProposalDecisionReceipt,
    type FileEditProposalDecision,
} from "../../../features/workbench/file-edit-proposal-decision-data.ts";

const UNCERTAIN =
    "提案决策结果未确认，请先查询详情，勿直接重复提交";

// 同时匹配HTTP状态和错误码，不转发上游文案或异常正文。
const ERRORS: Record<number, Record<string, string>> = {
    400: {
        invalid_workspace_request: "提案审批请求无法解析",
    },
    401: {
        invalid_login_session: "身份无效，请检查本地运行配置",
    },
    403: {
        local_mode_required: "任务功能仅支持本地模式",
        local_access_rejected: "本地服务拒绝访问，请检查运行配置",
        workspace_origin_rejected: "请求来源不被允许",
    },
    404: {
        workspace_not_accessible: "工作空间不存在或不可访问",
    },
    409: {
        proposal_binding_changed:
            "任务或项目目录绑定已变化，请重新生成提案",
        proposal_state_conflict:
            "提案已不处于待审批状态，请重新读取详情",
        proposal_diff_incomplete:
            "提案Diff已截断，不能直接批准",
    },
    415: {
        unsupported_workspace_content_type:
            "提案审批请求必须使用application/json",
    },
    422: {
        invalid_proposal_decision_input:
            "提案审批请求参数不符合要求",
        proposal_decision_invalid:
            "提案决策只能是批准或拒绝",
    },
    500: {
        proposal_decision_uncertain: UNCERTAIN,
    },
};

function failure(
    status: number,
    code: string,
    message: string,
): Response {
    return Response.json(
        { code, message },
        {
            status,
            headers: { "Cache-Control": "no-store" },
        },
    );
}

function notSubmitted(): Response {
    // 只在尚未调用上游fetch的阶段使用。
    return failure(
        499,
        "proposal_request_cancelled",
        "请求已取消，本次请求尚未转发审批",
    );
}

function uncertain(status: number): Response {
    return failure(
        status,
        "proposal_decision_uncertain",
        UNCERTAIN,
    );
}

export async function fileEditProposalDecisionProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): Promise<Response> {
    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return failure(
                403,
                "local_mode_required",
                "任务功能仅支持本地模式",
            );
        }

        // 检查本地Host、后端地址、来源和服务端凭证配置。
        // 不读取浏览器提供的Cookie或内部凭证作为可信身份。
        headers = localHeaders(request);
    } catch {
        return failure(
            403,
            "local_access_rejected",
            "本地服务请求被拒绝，请检查运行配置",
        );
    }

    // localHeaders允许只读请求省略Origin；审批写操作必须提供。
    // 非空Origin的允许列表校验已经由localHeaders完成。
    const origin = request.headers.get("origin");
    if (!origin) {
        return failure(
            403,
            "workspace_origin_rejected",
            "请求来源不被允许",
        );
    }

    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || !isProposalIdentifier(proposalId)
        || new URL(request.url).searchParams.size !== 0
    ) {
        return failure(
            422,
            "invalid_proposal_decision_input",
            "提案审批请求参数不符合要求",
        );
    }

    const contentType = request.headers
        .get("content-type")
        ?.split(";", 1)[0]
        .trim()
        .toLowerCase();

    if (contentType !== "application/json") {
        return failure(
            415,
            "unsupported_workspace_content_type",
            "提案审批请求必须使用application/json",
        );
    }

    if (request.signal.aborted) {
        return notSubmitted();
    }

    let decision: FileEditProposalDecision;

    try {
        const raw: unknown = await request.json();

        // 读取浏览器正文期间取消，也不能继续转发。
        if (request.signal.aborted) {
            return notSubmitted();
        }

        const parsed = readFileEditProposalDecision(raw);
        if (parsed === null) {
            return failure(
                422,
                "invalid_proposal_decision_input",
                "提案审批请求参数不符合要求",
            );
        }

        decision = parsed;
    } catch {
        if (request.signal.aborted) {
            return notSubmitted();
        }

        return failure(
            422,
            "invalid_proposal_decision_input",
            "提案审批请求正文无法解析",
        );
    }

    // 20秒预算覆盖上游请求和上游响应正文读取。
    // 浏览器请求正文的接收不计入此上游预算。
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);

    let forwardingStarted = false;

    try {
        signal.throwIfAborted();

        // 从开始调用fetch起，任何异常都不能证明后端没有提交。
        forwardingStarted = true;

        const response = await fetch(
            `${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}`
                + `/workspaces/${workspaceId}/tasks/${taskId}`
                + `/file-edit-proposals/${proposalId}/decision`,
            {
                method: "POST",
                headers: {
                    ...headers,
                    Origin: origin,
                    "Content-Type": "application/json",
                },
                // 重新构造严格正文，不直接透传浏览器的原始内容。
                body: JSON.stringify({ decision }),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        signal.throwIfAborted();

        // 不识别的HTTP状态不消费正文，也不尝试解释其中的信息。
        if (
            response.status !== 200
            && !Object.hasOwn(ERRORS, response.status)
        ) {
            await response.body?.cancel();
            signal.throwIfAborted();
            return uncertain(502);
        }

        const raw: unknown = await response.json();

        // 取消和超时也覆盖成功/错误响应的正文解析阶段。
        signal.throwIfAborted();

        if (response.status === 200) {
            const receipt = readFileEditProposalDecisionReceipt(
                raw,
                workspaceId,
                taskId,
                proposalId,
                decision,
            );

            if (receipt === null) {
                return uncertain(502);
            }

            // 不复制上游Set-Cookie或其他响应头。
            return Response.json(receipt, {
                headers: { "Cache-Control": "no-store" },
            });
        }

        if (raw && typeof raw === "object" && !Array.isArray(raw)) {
            const record = raw as Record<string, unknown>;
            const allowed = ERRORS[response.status];

            if (
                typeof record.code === "string"
                && Object.hasOwn(allowed, record.code)
            ) {
                return failure(
                    response.status,
                    record.code,
                    allowed[record.code],
                );
            }
        }

        // 状态已知但错误码未知，仍不能信任上游正文。
        return uncertain(502);
    } catch {
        if (!forwardingStarted && request.signal.aborted) {
            return notSubmitted();
        }

        // 取消fetch不撤销数据库事务；不自动重试或宣称审批失败。
        return uncertain(
            request.signal.aborted
                ? 499
                : timeout.aborted
                    ? 504
                    : 502,
        );
    }
}
