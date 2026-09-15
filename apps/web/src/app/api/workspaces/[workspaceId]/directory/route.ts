import { isAbsolute } from "node:path";

import {
    isLocalMode,
    localHeaders,
} from "../../../_shared/runtime.ts";

export const runtime = "nodejs";

const API_BASE_URL =
    process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

const ALLOWED_ORIGINS = new Set(
    (
        process.env.AUTH_ALLOWED_ORIGINS ??
        "http://localhost:3000,http://127.0.0.1:3000"
    )
        .split(",")
        .map((origin) => origin.trim())
        .filter(Boolean),
);

const BINDING_TIMEOUT_MS = 10_000;

type BindingContext = {
    params: Promise<{
        workspaceId: string;
    }>;
};

// 状态码和错误码必须同时匹配；文案由 BFF 提供，不透传原始异常。
const BACKEND_ERRORS: Record<number, Record<string, string>> = {
    400: {
        invalid_workspace_request: "工作空间请求无法解析",
    },
    403: {
        local_mode_required: "项目目录绑定仅支持本地模式",
        local_access_rejected: "本地服务拒绝访问，请检查运行配置",
        workspace_origin_rejected: "工作空间请求来源不被允许",
    },
    404: {
        workspace_not_accessible: "工作空间不存在或不可访问",
    },
    409: {
        workspace_already_bound: "工作空间已绑定其他项目目录",
    },
    415: {
        unsupported_workspace_content_type:
            "工作空间请求必须使用 application/json",
    },
    422: {
        invalid_workspace_input: "工作空间请求参数不符合要求",
        invalid_directory_path: "项目目录路径不符合要求",
        directory_not_found: "项目目录不存在或路径中包含非目录项",
        directory_access_denied: "没有权限访问项目目录",
        not_a_directory: "请选择目录，而不是文件",
        root_directory_not_allowed: "不能将文件系统根目录作为项目目录",
    },
    500: {
        workspace_binding_failed: "项目目录绑定结果未确认，请稍后检查",
    },
    503: {
        directory_unavailable: "暂时无法访问项目目录，请稍后再试",
    },
};

// 读取与绑定使用独立错误契约，避免把查询失败描述成写入失败。
const READ_BACKEND_ERRORS: Record<number, Record<string, string>> = {
    403: {
        local_mode_required: "项目目录功能仅支持本地模式",
        local_access_rejected: "本地服务拒绝访问，请检查运行配置",
    },
    404: {
        workspace_not_accessible: "工作空间不存在或不可访问",
    },
    422: {
        invalid_workspace_input: "工作空间标识不符合要求",
    },
    500: {
        workspace_directory_read_failed:
            "读取项目目录状态失败，请稍后重试",
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

function invalidBackendResponse(): Response {
    // 收到异常响应时，后端可能已经完成提交，不能断言绑定失败。
    return errorResponse(
        502,
        "invalid_backend_response",
        "目录绑定服务返回异常，绑定结果尚未确认",
    );
}

function cancelledResponse(): Response {
    // 中断代理请求不等于撤销后端已经提交的数据库事务。
    return errorResponse(
        499,
        "workspace_binding_cancelled",
        "目录绑定请求已中断，绑定结果尚未确认",
    );
}

function readFailure(): Response {
    // 异常响应表示无法确认状态，不能用 root_path: null 替代。
    return errorResponse(
        502,
        "invalid_backend_response",
        "目录状态服务返回异常，请稍后重试",
    );
}

export async function PUT(
    request: Request,
    context: BindingContext,
): Promise<Response> {
    let runtimeHeaders: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return errorResponse(
                403,
                "local_mode_required",
                "项目目录绑定仅支持本地模式",
            );
        }

        // 复用本地 Host、上游地址、来源和内部凭证检查。
        // 内部凭证只从服务端配置读取，不采用浏览器传来的值。
        runtimeHeaders = localHeaders(request);
    } catch {
        return errorResponse(
            403,
            "local_access_rejected",
            "本地服务请求被拒绝，请检查运行配置",
        );
    }

    const origin = request.headers.get("origin");

    // 写操作必须携带允许的 Origin，即使同源也不能省略。
    if (origin === null || !ALLOWED_ORIGINS.has(origin)) {
        return errorResponse(
            403,
            "workspace_origin_rejected",
            "工作空间请求来源不被允许",
        );
    }

    const contentType = (
        request.headers.get("content-type") ?? ""
    )
        .split(";", 1)[0]
        .trim()
        .toLowerCase();

    if (contentType !== "application/json") {
        return errorResponse(
            415,
            "unsupported_workspace_content_type",
            "工作空间请求必须使用 application/json",
        );
    }

    const { workspaceId } = await context.params;

    // 先限制动态路径格式，再拼接上游 URL。
    if (!/^[0-9a-f]{32}$/.test(workspaceId)) {
        return errorResponse(
            422,
            "invalid_workspace_input",
            "工作空间标识不符合要求",
        );
    }

    if (request.signal.aborted) {
        return cancelledResponse();
    }

    let body: unknown;

    try {
        body = await request.json();
    } catch {
        if (request.signal.aborted) {
            return cancelledResponse();
        }

        return errorResponse(
            400,
            "invalid_workspace_request",
            "工作空间请求无法解析",
        );
    }

    // 严格限定正文只有 root_path；不接受身份、额外配置或其他字段。
    // 不 trim 路径，实际目录存在性和规范化由 FastAPI 统一处理。
    if (
        !isRecord(body) ||
        Object.keys(body).length !== 1 ||
        typeof body.root_path !== "string" ||
        body.root_path.length === 0
    ) {
        return errorResponse(
            422,
            "invalid_workspace_input",
            "请求正文必须只包含非空的 root_path 字符串",
        );
    }

    const timeoutSignal = AbortSignal.timeout(BINDING_TIMEOUT_MS);
    const signal = AbortSignal.any([
        request.signal,
        timeoutSignal,
    ]);

    try {
        signal.throwIfAborted();

        const backendResponse = await fetch(
            `${API_BASE_URL}/workspaces/${workspaceId}/directory`,
            {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                    Origin: origin,
                    ...runtimeHeaders,
                },
                body: JSON.stringify({
                    root_path: body.root_path,
                }),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        let payload: unknown;

        try {
            payload = await backendResponse.json();
        } catch {
            // 读取响应正文也可能超时或被取消，优先交给外层分类。
            signal.throwIfAborted();
            return invalidBackendResponse();
        }

        signal.throwIfAborted();

        if (!isRecord(payload)) {
            return invalidBackendResponse();
        }

        if (backendResponse.status !== 200) {
            const allowedErrors = BACKEND_ERRORS[backendResponse.status];

            // 使用 Object.hasOwn，不能把原型上的属性当成合法错误码。
            if (
                typeof payload.code !== "string" ||
                !allowedErrors ||
                !Object.hasOwn(allowedErrors, payload.code)
            ) {
                return invalidBackendResponse();
            }

            return errorResponse(
                backendResponse.status,
                payload.code,
                allowedErrors[payload.code],
            );
        }

        // 返回标识必须与本次请求完全一致，防止接受其他 Workspace 的结果。
        // 本地模式下 Web/API 在同一电脑，使用当前系统的绝对路径规则。
        if (
            payload.external_id !== workspaceId ||
            typeof payload.name !== "string" ||
            Array.from(payload.name).length < 1 ||
            Array.from(payload.name).length > 100 ||
            typeof payload.root_path !== "string" ||
            payload.root_path.includes("\0") ||
            !isAbsolute(payload.root_path)
        ) {
            return invalidBackendResponse();
        }

        // 后端可能返回额外字段，只复制明确允许的三个字段。
        // 不转发 Set-Cookie、内部响应头或数据库主键。
        return Response.json(
            {
                external_id: payload.external_id,
                name: payload.name,
                root_path: payload.root_path,
            },
            {
                status: 200,
                headers: { "Cache-Control": "no-store" },
            },
        );
    } catch {
        if (request.signal.aborted) {
            return cancelledResponse();
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "workspace_binding_timeout",
                "目录绑定请求超时，绑定结果尚未确认",
            );
        }

        return errorResponse(
            502,
            "workspace_binding_unavailable",
            "目录绑定服务暂时不可用，绑定结果尚未确认",
        );
    }
}

export async function GET(
    request: Request,
    context: BindingContext,
): Promise<Response> {
    let runtimeHeaders: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return errorResponse(
                403,
                "local_mode_required",
                "项目目录功能仅支持本地模式",
            );
        }

        // 复用本地 Host、上游地址、跨站请求及内部凭证检查。
        // 浏览器传入的 Cookie 和内部凭证都不会直接转发。
        runtimeHeaders = localHeaders(request);
    } catch {
        return errorResponse(
            403,
            "local_access_rejected",
            "本地服务请求被拒绝，请检查运行配置",
        );
    }

    const origin = request.headers.get("origin");

    // 同源 GET 可能没有 Origin；只在存在时检查并转发。
    if (origin !== null && !ALLOWED_ORIGINS.has(origin)) {
        return errorResponse(
            403,
            "workspace_origin_rejected",
            "工作空间请求来源不被允许",
        );
    }

    const { workspaceId } = await context.params;

    if (!/^[0-9a-f]{32}$/.test(workspaceId)) {
        return errorResponse(
            422,
            "invalid_workspace_input",
            "工作空间标识不符合要求",
        );
    }

    // 读取沿用 10 秒上游超时，信号同时覆盖 fetch 与响应正文读取。
    const timeoutSignal = AbortSignal.timeout(BINDING_TIMEOUT_MS);
    const signal = AbortSignal.any([
        request.signal,
        timeoutSignal,
    ]);

    try {
        signal.throwIfAborted();

        const backendResponse = await fetch(
            `${API_BASE_URL}/workspaces/${workspaceId}/directory`,
            {
                method: "GET",
                headers: {
                    ...(origin === null ? {} : { Origin: origin }),
                    ...runtimeHeaders,
                },
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        let payload: unknown;

        try {
            payload = await backendResponse.json();
        } catch {
            // 中断优先交给外层处理；其他解析错误视为异常响应。
            signal.throwIfAborted();
            return readFailure();
        }

        signal.throwIfAborted();

        if (!isRecord(payload)) {
            return readFailure();
        }

        if (backendResponse.status !== 200) {
            const allowedErrors =
                READ_BACKEND_ERRORS[backendResponse.status];

            // 只接受状态码与自有错误码匹配的组合。
            // 不反射后端 message，也不接受原型上的属性名称。
            if (
                typeof payload.code !== "string" ||
                !allowedErrors ||
                !Object.hasOwn(allowedErrors, payload.code)
            ) {
                return readFailure();
            }

            return errorResponse(
                backendResponse.status,
                payload.code,
                allowedErrors[payload.code],
            );
        }

        // 成功响应必须属于当前请求的工作空间。
        // root_path 必须存在，只有明确的 null 才表示未绑定。
        if (
            payload.external_id !== workspaceId ||
            typeof payload.name !== "string" ||
            Array.from(payload.name).length < 1 ||
            Array.from(payload.name).length > 100 ||
            !Object.hasOwn(payload, "root_path") ||
            (
                payload.root_path !== null &&
                (
                    typeof payload.root_path !== "string" ||
                    payload.root_path.includes("\0") ||
                    !isAbsolute(payload.root_path)
                )
            )
        ) {
            return readFailure();
        }

        // 显式保留 null，白名单复制字段，不透传内部字段与响应头。
        return Response.json(
            {
                external_id: payload.external_id,
                name: payload.name,
                root_path: payload.root_path,
            },
            {
                status: 200,
                headers: { "Cache-Control": "no-store" },
            },
        );
    } catch {
        if (request.signal.aborted) {
            return errorResponse(
                499,
                "workspace_directory_read_cancelled",
                "目录状态查询已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "workspace_directory_read_timeout",
                "目录状态查询超时，请稍后重试",
            );
        }

        return errorResponse(
            502,
            "workspace_directory_read_unavailable",
            "目录状态服务暂时不可用，请稍后重试",
        );
    }
}
