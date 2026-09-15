import { isLocalMode, localHeaders } from "../_shared/runtime.ts";
import { readWorkspaceList } from "../../../features/workspaces/workspace-list.ts";

export const runtime = "nodejs";

// 后端地址仅在服务端使用，不暴露给浏览器。
const API_BASE_URL =
    process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

// 沿用认证接口的来源配置，使用完整 Origin 精确匹配。
const ALLOWED_ORIGINS = new Set(
    (
        process.env.AUTH_ALLOWED_ORIGINS ??
        "http://localhost:3000,http://127.0.0.1:3000"
    )
        .split(",")
        .map((origin) => origin.trim())
        .filter(Boolean),
);

const LOGIN_COOKIE_NAME = "agent_session";
const WORKSPACE_TIMEOUT_MS = 10_000;

type SafeError = {
    code: string;
    message: string;
};

// 同一 HTTP 状态可能对应多个业务错误码，例如两种 422。
const BACKEND_ERRORS: Record<number, SafeError[]> = {
    400: [
        {
            code: "invalid_workspace_request",
            message: "工作空间请求无法解析",
        },
    ],
    401: [
        {
            code: "invalid_login_session",
            message: "登录状态无效，请重新登录",
        },
    ],
    403: [
        {
            code: "workspace_origin_rejected",
            message: "工作空间请求来源不被允许",
        },
    ],
    415: [
        {
            code: "unsupported_workspace_content_type",
            message: "工作空间请求必须使用 application/json",
        },
    ],
    422: [
        {
            code: "invalid_workspace_input",
            message: "工作空间创建信息不符合要求",
        },
        {
            code: "invalid_workspace_name",
            message: "工作空间名称去除首尾空白后须为 1～100 个字符",
        },
    ],
    500: [
        {
            code: "workspace_creation_failed",
            message: "创建工作空间失败，请稍后再试",
        },
    ],
};

// 查询与创建使用不同的错误契约，避免把读取失败描述为创建失败。
const LIST_BACKEND_ERRORS: Record<number, SafeError> = {
    400: {
        code: "invalid_workspace_request",
        message: "工作空间查询请求无法解析",
    },
    401: {
        code: "invalid_login_session",
        message: "登录状态无效，请重新登录",
    },
    403: {
        code: "local_access_rejected",
        message: "本地服务拒绝访问，请检查运行配置",
    },
    422: {
        code: "invalid_workspace_input",
        message: "工作空间查询参数不符合要求",
    },
    500: {
        code: "workspace_list_failed",
        message: "读取工作空间列表失败，请稍后再试",
    },
};

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
    return errorResponse(
        502,
        "invalid_backend_response",
        "工作空间服务返回异常",
    );
}

function cancelledResponse(): Response {
    return errorResponse(
        499,
        "workspace_request_cancelled",
        "工作空间创建请求已取消",
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

function readLoginToken(cookieHeader: string | null): string | null {
    if (cookieHeader === null) {
        return null;
    }

    let token: string | null = null;

    for (const segment of cookieHeader.split(";")) {
        const pair = segment.trimStart();
        const separator = pair.indexOf("=");

        if (
            separator === -1 ||
            pair.slice(0, separator) !== LOGIN_COOKIE_NAME
        ) {
            continue;
        }

        const value = pair.slice(separator + 1);

        // 重复的登录 Cookie 或不符合格式的令牌直接拒绝。
        // 格式合法不代表身份有效，后端仍需查询登录会话。
        if (token !== null || !/^[A-Za-z0-9_-]{43}$/.test(value)) {
            return null;
        }

        token = value;
    }

    return token;
}

export async function POST(request: Request): Promise<Response> {
    const origin = request.headers.get("origin");

    if (origin === null || !ALLOWED_ORIGINS.has(origin)) {
        return errorResponse(
            403,
            "workspace_origin_rejected",
            "工作空间请求来源不被允许",
        );
    }

    // 允许 application/json; charset=utf-8 等参数形式。
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

    const token = readLoginToken(request.headers.get("cookie"));

    if (!isLocalMode() && token === null) {
        return errorResponse(
            401,
            "invalid_login_session",
            "登录状态无效，请重新登录",
        );
    }

    let requestBody: unknown;

    try {
        requestBody = await request.json();
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

    // 只接受 name，不允许浏览器提交 user_id 等额外字段。
    // 名称去空白和长度规则交给后端统一处理。
    if (
        !isRecord(requestBody) ||
        Object.keys(requestBody).length !== 1 ||
        typeof requestBody.name !== "string"
    ) {
        return errorResponse(
            422,
            "invalid_workspace_input",
            "工作空间创建信息不符合要求",
        );
    }

    // 浏览器取消或后端超时，都可以中止这次代理请求。
    const timeoutSignal = AbortSignal.timeout(WORKSPACE_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/workspaces`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Origin: origin,
                    ...(isLocalMode() ? localHeaders(request) : { Cookie: `${LOGIN_COOKIE_NAME}=${token}` }),
                },
                body: JSON.stringify({ name: requestBody.name }),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        let payload: unknown;

        try {
            payload = await backendResponse.json();
        } catch {
            // 取消和超时由外层统一分类，其余解析失败视为响应异常。
            signal.throwIfAborted();
            return invalidBackendResponse();
        }

        if (!isRecord(payload)) {
            return invalidBackendResponse();
        }

        if (backendResponse.status !== 201) {
            // 状态码和业务错误码必须同时匹配已知契约。
            // 使用本地安全文案，不转发后端原始 message 或异常内容。
            const safeError = BACKEND_ERRORS[
                backendResponse.status
            ]?.find((item) => item.code === payload.code);

            if (!safeError) {
                return invalidBackendResponse();
            }

            return errorResponse(
                backendResponse.status,
                safeError.code,
                safeError.message,
            );
        }

        // 后端返回的数据也需要运行时检查，TypeScript 类型无法替代它。
        if (
            typeof payload.external_id !== "string" ||
            !/^[0-9a-f]{32}$/.test(payload.external_id) ||
            typeof payload.name !== "string" ||
            Array.from(payload.name).length < 1 ||
            Array.from(payload.name).length > 100 ||
            typeof payload.created_at !== "string" ||
            !Number.isFinite(Date.parse(payload.created_at))
        ) {
            return invalidBackendResponse();
        }

        // 只返回公开字段，不透传数据库主键、归属信息或后端响应头。
        return Response.json(
            {
                external_id: payload.external_id,
                name: payload.name,
                created_at: payload.created_at,
            },
            {
                status: 201,
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
                "workspace_backend_timeout",
                "工作空间服务响应超时，创建结果尚未确认",
            );
        }

        return errorResponse(
            502,
            "workspace_backend_unavailable",
            "工作空间服务暂时不可用，创建结果尚未确认",
        );
    }
}

export async function GET(request: Request): Promise<Response> {
    const origin = request.headers.get("origin");

    // 同源 GET 可能没有 Origin；存在时必须匹配允许来源。
    if (
        (origin !== null && !ALLOWED_ORIGINS.has(origin)) ||
        request.headers.get("sec-fetch-site") === "cross-site"
    ) {
        return errorResponse(
            403,
            "workspace_origin_rejected",
            "工作空间请求来源不被允许",
        );
    }

    const token = readLoginToken(request.headers.get("cookie"));

    if (!isLocalMode() && token === null) {
        return errorResponse(
            401,
            "invalid_login_session",
            "登录状态无效，请重新登录",
        );
    }

    const limits = new URL(request.url).searchParams.getAll("limit");
    const rawLimit = limits[0] ?? "20";

    // 不接受重复参数、小数或超出范围的值，只转发这个明确允许的参数。
    if (
        limits.length > 1 ||
        !/^(?:[1-9]\d?|100)$/.test(rawLimit)
    ) {
        return errorResponse(
            422,
            "invalid_workspace_input",
            "查询数量必须为 1～100 的整数",
        );
    }

    const limit = Number(rawLimit);
    const timeoutSignal = AbortSignal.timeout(WORKSPACE_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/workspaces?limit=${limit}`,
            {
                method: "GET",
                headers: {
                    ...(origin === null ? {} : { Origin: origin }),
                    ...(isLocalMode()
                        ? localHeaders(request)
                        : { Cookie: `${LOGIN_COOKIE_NAME}=${token}` }),
                },
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        const payload: unknown = await backendResponse.json();

        if (backendResponse.status !== 200) {
            const safeError =
                LIST_BACKEND_ERRORS[backendResponse.status];

            if (
                !isRecord(payload) ||
                !safeError ||
                payload.code !== safeError.code
            ) {
                return invalidBackendResponse();
            }

            return errorResponse(
                backendResponse.status,
                safeError.code,
                safeError.message,
            );
        }

        const data = readWorkspaceList(payload, limit);

        if (data === null) {
            return invalidBackendResponse();
        }

        return Response.json(data, {
            status: 200,
            headers: { "Cache-Control": "no-store" },
        });
    } catch (error) {
        if (request.signal.aborted) {
            return errorResponse(
                499,
                "workspace_list_cancelled",
                "工作空间查询已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "workspace_list_timeout",
                "工作空间查询超时，请稍后重试",
            );
        }

        if (error instanceof SyntaxError) {
            return invalidBackendResponse();
        }

        return errorResponse(
            502,
            "workspace_list_unavailable",
            "工作空间服务暂时不可用，请稍后重试",
        );
    }
}
