import { isLocalMode } from "../../_shared/runtime.ts";

export const runtime = "nodejs";

const API_BASE_URL =
    process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

const ALLOWED_ORIGINS = (
    process.env.AUTH_ALLOWED_ORIGINS ??
    "http://localhost:3000,http://127.0.0.1:3000"
)
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);

const LOGIN_TIMEOUT_MS = 10_000;

type SafeError = {
    code: string;
    message: string;
};

const BACKEND_ERRORS: Record<number, SafeError> = {
    400: {
        code: "request_rejected",
        message: "请求无法处理",
    },
    401: {
        code: "invalid_credentials",
        message: "用户名或密码错误",
    },
    403: {
        code: "login_origin_rejected",
        message: "登录请求来源不被允许",
    },
    415: {
        code: "unsupported_login_content_type",
        message: "登录请求必须使用 application/json",
    },
    422: {
        code: "invalid_login_input",
        message: "登录信息不符合要求，请检查用户名和密码",
    },
    500: {
        code: "login_failed",
        message: "登录失败，请稍候再试",
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

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

function invalidBackendResponse(): Response {
    return errorResponse(
        502,
        "invalid_backend_response",
        "登录服务返回异常，请稍候再试",
    );
}

export async function POST(request: Request): Promise<Response> {
    if (isLocalMode()) return Response.json({ code: "local_account_disabled", message: "本地模式无需账号登录" }, { status: 403, headers: { "Cache-Control": "no-store" } });
    const origin = request.headers.get("origin");

    if (origin === null || !ALLOWED_ORIGINS.includes(origin)) {
        return errorResponse(
            403,
            "login_origin_rejected",
            "登录请求来源不被允许",
        );
    }

    const contentType = request.headers
        .get("content-type")
        ?.split(";")[0]
        .trim()
        .toLowerCase();

        if (contentType !== "application/json") {
        return errorResponse(
            415,
            "unsupported_login_content_type",
            "登录请求必须使用 application/json",
        );
    }

    let requestBody: unknown;

    try {
        requestBody = await request.json()
    }
    catch {
        return errorResponse(
            400,
            "invalid_login_json",
            "登录请求不是有效的 JSON",
        );
    }

    const timeoutSignal = AbortSignal.timeout(LOGIN_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/auth/login`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Origin: origin,
                },
                body: JSON.stringify(requestBody),
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        const payload: unknown = await backendResponse.json();

        if (!isRecord(payload)) {
            return invalidBackendResponse();
        }

        if (backendResponse.status !== 200) {
            const safeError = BACKEND_ERRORS[backendResponse.status];

            if (!safeError || payload.code !== safeError.code) {
                return invalidBackendResponse();
            }

            return errorResponse(
                backendResponse.status,
                safeError.code,
                safeError.message,
            );
        }

        if (
            typeof payload.external_id !== "string" ||
            typeof payload.username !== "string"
        ) {
            return invalidBackendResponse();
        }

        const setCookies = backendResponse.headers.getSetCookie();

        if (
            !setCookies.some((cookie) =>
                cookie.startsWith("agent_session="),
            )
        ) {
            return invalidBackendResponse();
        }

        const response = Response.json(
            {
                external_id: payload.external_id,
                username: payload.username,
            },
            {
                status: 200,
                headers: { "Cache-Control": "no-store" },
            },
        );

        for (const cookie of setCookies) {
            response.headers.append("Set-Cookie", cookie);
        }

        return response;
    }
    catch {
        if (request.signal.aborted) {
            return errorResponse(
                499,
                "login_request_cancelled",
                "登录请求已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "login_backend_timeout",
                "登录服务响应超时，请稍候再试",
            );
        }

        return errorResponse(
            502,
            "login_backend_unavailable",
            "登录服务暂时不可用，请稍候再试",
        );
    }
}