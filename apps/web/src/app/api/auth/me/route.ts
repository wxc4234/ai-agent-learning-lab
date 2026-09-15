import { isLocalMode, localHeaders } from "../../_shared/runtime.ts";

export const runtime = "nodejs";

const API_BASE_URL =
    process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

const LOGIN_COOKIE_NAME = "agent_session";
const CURRENT_USER_TIMEOUT_MS = 10_000;

type SafeError = {
    code: string;
    message: string;
};

const BACKEND_ERRORS: Record<number, SafeError> = {
    401: {
        code: "invalid_login_session",
        message: "登录状态无效，请重新登录",
    },
    500: {
        code: "current_user_failed",
        message: "获取当前用户失败，请稍候再试",
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

function unauthorizedResponse(): Response {
    return errorResponse(
        401,
        "invalid_login_session",
        "登录状态无效，请重新登录",
    );
}

function invalidBackendResponse(): Response {
    return errorResponse(
        502,
        "invalid_backend_response",
        "身份服务返回异常，请稍候再试",
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

        if (token !== null || !/^[A-Za-z0-9_-]{43}$/.test(value)) {
            return null;
        }

        token = value;
    }

    return token;
}

export async function GET(request: Request): Promise<Response> {
    const token = readLoginToken(request.headers.get("cookie"));

    if (!isLocalMode() && token === null) {
        return unauthorizedResponse();
    }

    const timeoutSignal = AbortSignal.timeout(CURRENT_USER_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/auth/me`,
            {
                method: "GET",
                headers: {
                    ...(isLocalMode() ? localHeaders(request) : { Cookie: `${LOGIN_COOKIE_NAME}=${token}` }),
                },
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

        return Response.json(
            {
                external_id: payload.external_id,
                username: payload.username,
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
                "current_user_request_cancelled",
                "身份查询已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "current_user_backend_timeout",
                "身份服务响应超时，请稍候再试",
            );
        }

        return errorResponse(
            502,
            "current_user_backend_unavailable",
            "身份服务暂时不可用，请稍候再试",
        );
    }
}