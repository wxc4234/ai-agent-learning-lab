import {
    isLocalMode,
    localHeaders,
} from "../../../_shared/runtime.ts";

export const runtime = "nodejs";

const TASK_TIMEOUT_MS = 10_000;
const IDENTIFIER = /^[0-9a-f]{32}$/;

type TaskContext = {
    params: Promise<{
        workspaceId: string;
    }>;
};

// HTTP 状态和业务错误码必须同时匹配。
// 文案由 BFF 提供，不直接渲染后端返回的任意 message。
const BACKEND_ERRORS: Record<number, Record<string, string>> = {
    400: {
        invalid_workspace_request: "任务请求无法解析",
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
        task_creation_conflict: "该请求键已用于不同的任务创建内容",
        task_creation_result_deleted: "该请求对应的任务已删除，请使用新的请求键创建",
    },
    415: {
        unsupported_workspace_content_type: "任务请求必须使用 JSON",
    },
    422: {
        invalid_task_input: "任务请求参数不符合要求",
        invalid_task_title: "任务标题去除首尾空白后须为 1～200 个字符",
        invalid_task_request_key: "任务创建请求键须为 32 位小写十六进制字符串",
    },
    500: {
        task_creation_uncertain: "任务创建结果未确认，请勿直接重复提交",
    },
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

function errorResponse(
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

function uncertainResponse(status = 502): Response {
    // 网络异常、超时或畸形响应都不能证明后端没有提交。
    return errorResponse(
        status,
        "task_creation_uncertain",
        "任务创建结果未确认，请勿直接重复提交",
    );
}

export async function POST(
    request: Request,
    context: TaskContext,
): Promise<Response> {
    let runtimeHeaders: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return errorResponse(
                403,
                "local_mode_required",
                "任务功能仅支持本地模式",
            );
        }

        // 复用本地 Host、后端地址、Origin 和跨站请求检查。
        // 内部凭证来自服务端配置，不采用浏览器传入的凭证。
        runtimeHeaders = localHeaders(request);
    } catch {
        return errorResponse(
            403,
            "local_access_rejected",
            "本地服务请求被拒绝，请检查运行配置",
        );
    }

    const origin = request.headers.get("origin");

    // localHeaders 已检查存在的 Origin；创建请求还必须提供 Origin。
    if (!origin) {
        return errorResponse(
            403,
            "workspace_origin_rejected",
            "请求来源不被允许",
        );
    }

    const contentType = request.headers
        .get("content-type")
        ?.split(";", 1)[0]
        .trim()
        .toLowerCase();

    if (contentType !== "application/json") {
        return errorResponse(
            415,
            "unsupported_workspace_content_type",
            "任务请求必须使用 JSON",
        );
    }

    const { workspaceId } = await context.params;

    if (!IDENTIFIER.test(workspaceId)) {
        return errorResponse(
            422,
            "invalid_task_input",
            "工作空间标识不符合要求",
        );
    }

    // 请求尚未转发时取消，可以明确知道本次没有提交到后端。
    if (request.signal.aborted) {
        return errorResponse(
            499,
            "task_request_cancelled",
            "请求已取消，尚未提交任务",
        );
    }

    let body: unknown;

    try {
        body = await request.json();
    } catch {
        return errorResponse(
            request.signal.aborted ? 499 : 400,
            request.signal.aborted
                ? "task_request_cancelled"
                : "invalid_task_request",
            request.signal.aborted
                ? "请求已取消，尚未提交任务"
                : "任务请求无法解析",
        );
    }

    // 仅接受创建内容与可选请求键，不接受身份、指纹或资源内部主键。
    // 不在 BFF 重复 strip，避免两端维护不同的标题规范化规则。
    if (
        !isRecord(body) ||
        Object.keys(body).some(key => key !== "title" && key !== "request_key") ||
        !Object.hasOwn(body, "title") ||
        typeof body.title !== "string" ||
        (
            Object.hasOwn(body, "request_key") &&
            body.request_key !== null &&
            (
                typeof body.request_key !== "string" ||
                body.request_key.length !== 32 ||
                !IDENTIFIER.test(body.request_key)
            )
        )
    ) {
        return errorResponse(
            422,
            "invalid_task_input",
            "任务请求须包含字符串标题及可选的 32 位小写十六进制请求键",
        );
    }

    if (request.signal.aborted) {
        return errorResponse(
            499,
            "task_request_cancelled",
            "请求已取消，尚未提交任务",
        );
    }

    const timeoutSignal = AbortSignal.timeout(TASK_TIMEOUT_MS);
    const signal = AbortSignal.any([
        request.signal,
        timeoutSignal,
    ]);

    try {
        signal.throwIfAborted();

        const response = await fetch(
            `${
                process.env.API_BASE_URL ?? "http://127.0.0.1:8000"
            }/workspaces/${workspaceId}/tasks`,
            {
                method: "POST",
                headers: {
                    ...runtimeHeaders,
                    Origin: origin,
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    title: body.title,
                    // 缺省保持旧 UI 行为；显式 null 与合法键原样转发。
                    // 键属于一次创建意图，BFF 不生成、改写或自动重试。
                    ...(Object.hasOwn(body, "request_key")
                        ? { request_key: body.request_key }
                        : {}),
                }),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        const payload: unknown = await response.json();

        // 超时与取消同时覆盖 fetch 和响应正文读取。
        signal.throwIfAborted();

        if (!isRecord(payload)) {
            return uncertainResponse();
        }

        if (response.status !== 201) {
            const allowedErrors = BACKEND_ERRORS[response.status];

            // 排除未知错误码、状态错配和原型上的属性名称。
            if (
                typeof payload.code !== "string" ||
                !allowedErrors ||
                !Object.hasOwn(allowedErrors, payload.code)
            ) {
                return uncertainResponse();
            }

            return errorResponse(
                response.status,
                payload.code,
                allowedErrors[payload.code],
            );
        }

        // 成功响应必须对应当前项目，并包含合法的公开字段。
        // 类型正确不代表资源归属正确，因此还要比较 workspace_id。
        if (
            payload.workspace_id !== workspaceId ||
            typeof payload.external_id !== "string" ||
            !IDENTIFIER.test(payload.external_id) ||
            typeof payload.conversation_id !== "string" ||
            !IDENTIFIER.test(payload.conversation_id) ||
            typeof payload.title !== "string" ||
            Array.from(payload.title).length < 1 ||
            Array.from(payload.title).length > 200 ||
            typeof payload.created_at !== "string" ||
            !Number.isFinite(Date.parse(payload.created_at))
        ) {
            return uncertainResponse();
        }

        // 白名单复制字段，不透传内部字段、Set-Cookie 或其他上游响应头。
        return Response.json(
            {
                external_id: payload.external_id,
                workspace_id: payload.workspace_id,
                conversation_id: payload.conversation_id,
                title: payload.title,
                created_at: payload.created_at,
            },
            {
                status: 201,
                headers: { "Cache-Control": "no-store" },
            },
        );
    } catch {
        // 请求开始转发后，取消不等于撤销后端事务。
        if (request.signal.aborted) {
            return uncertainResponse(499);
        }

        if (timeoutSignal.aborted) {
            return uncertainResponse(504);
        }

        return uncertainResponse();
    }
}


export async function GET(request: Request, context: TaskContext): Promise<Response> {
    const { taskProxy } = await import("../../../_shared/task-proxy.ts");
    return taskProxy(request, (await context.params).workspaceId);
}
