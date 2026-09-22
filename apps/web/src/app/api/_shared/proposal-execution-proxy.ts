import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";
import { readProposalExecutionReceipt } from "../../../features/workbench/proposal-execution-data.ts";

const UNCERTAIN =
    "本次执行结果未确认，不能据此判断文件未修改，请勿重复提交";

// 只接受明确的状态与错误码组合，使用本地固定文案。
const ERRORS: Record<number, Record<string, string>> = {
    400: {
        invalid_workspace_request: "提案应用请求无法解析",
    },
    401: {
        invalid_login_session: "身份无效，请检查本地运行配置",
    },
    403: {
        local_mode_required: "提案应用仅支持本地模式",
        local_access_rejected: "本地服务拒绝访问，请检查运行配置",
        workspace_origin_rejected: "请求来源不被允许",
    },
    404: {
        workspace_not_accessible: "资源不存在或不可访问",
    },
    409: {
        sample_execution_unavailable:
            "当前任务没有可用的服务端样例登记，不能启动本次应用",
    },
    415: {
        unsupported_workspace_content_type:
            "提案应用请求必须使用application/json",
    },
    422: {
        invalid_proposal_execution_input:
            "提案应用请求参数不符合要求",
    },
    500: {
        proposal_execution_uncertain: UNCERTAIN,
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
    // 仅表示本次请求尚未转发，不推断其他请求是否已经执行。
    return failure(
        499,
        "proposal_request_cancelled",
        "请求已取消，本次请求尚未转发应用",
    );
}

function uncertain(status: number): Response {
    return failure(
        status,
        "proposal_execution_uncertain",
        UNCERTAIN,
    );
}

function isApplyRequest(raw: unknown): boolean {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return false;
    }

    const record = raw as Record<string, unknown>;

    // 只允许一个明确动作，拒绝路径、身份、正文及跳过检查选项。
    return Object.keys(record).length === 1
        && Object.hasOwn(record, "action")
        && record.action === "apply";
}

export async function proposalExecutionProxy(
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
                "提案应用仅支持本地模式",
            );
        }

        // 使用服务端配置生成凭证，不透传浏览器Cookie或内部Token。
        // 同时核对本机地址、允许来源和后端地址。
        headers = localHeaders(request);
    } catch {
        return failure(
            403,
            "local_access_rejected",
            "本地服务请求被拒绝，请检查运行配置",
        );
    }

    // localHeaders允许只读请求省略Origin，写操作必须额外要求。
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
            "invalid_proposal_execution_input",
            "提案应用请求参数不符合要求",
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
            "提案应用请求必须使用application/json",
        );
    }

    if (request.signal.aborted) {
        return notSubmitted();
    }

    try {
        const raw: unknown = await request.json();

        // 正文读取期间取消，不能继续转发。
        if (request.signal.aborted) {
            return notSubmitted();
        }

        if (!isApplyRequest(raw)) {
            return failure(
                422,
                "invalid_proposal_execution_input",
                "提案应用正文只允许action=apply",
            );
        }
    } catch {
        if (request.signal.aborted) {
            return notSubmitted();
        }

        return failure(
            422,
            "invalid_proposal_execution_input",
            "提案应用请求正文无法解析",
        );
    }

    // 预算覆盖上游请求及响应正文读取，不包含浏览器正文接收。
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);
    let forwardingStarted = false;

    try {
        signal.throwIfAborted();

        // 从调用fetch开始，异常就不能证明后端没有产生副作用。
        forwardingStarted = true;

        const response = await fetch(
            `${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}`
                + `/workspaces/${workspaceId}/tasks/${taskId}`
                + `/file-edit-proposals/${proposalId}/apply`,
            {
                method: "POST",
                headers: {
                    ...headers,
                    Origin: origin,
                    "Content-Type": "application/json",
                },
                // 重建严格正文，不透传原始浏览器输入。
                body: JSON.stringify({ action: "apply" }),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        signal.throwIfAborted();

        // 未知状态不透传正文，也不跟随重定向再次发送写请求。
        if (
            response.status !== 200
            && !Object.hasOwn(ERRORS, response.status)
        ) {
            await response.body?.cancel();
            signal.throwIfAborted();
            return uncertain(502);
        }

        const raw: unknown = await response.json();
        signal.throwIfAborted();

        if (response.status === 200) {
            // 校验当前资源和合法状态组合，并剔除非公开字段。
            // unknown/uncertain是合法回执，不改写成成功或未执行。
            const receipt = readProposalExecutionReceipt(
                raw,
                workspaceId,
                taskId,
                proposalId,
            );

            if (receipt === null) {
                return uncertain(502);
            }

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
                // 不复制上游message、Set-Cookie或其他响应头。
                return failure(
                    response.status,
                    record.code,
                    allowed[record.code],
                );
            }
        }

        return uncertain(502);
    } catch {
        if (!forwardingStarted && request.signal.aborted) {
            return notSubmitted();
        }

        // 代理不持有数据库事务；取消网络请求不撤销后端写入。
        // 超时、断网、解析失败都不触发自动重试。
        return uncertain(
            request.signal.aborted
                ? 499
                : timeout.aborted
                    ? 504
                    : 502,
        );
    }
}
