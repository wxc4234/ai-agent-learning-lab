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

const LOGIN_COOKIE_NAME = "agent_session";
const LOGOUT_TIMEOUT_MS = 10_000;

type SafeError = {
    code: string;
    message: string;
};

type LoginCookie = {
    token: string | null;
    duplicate: boolean;
};

const BACKEND_ERRORS: Record<number, SafeError> = {
    403: {
        code: "logout_origin_rejected",
        message: "登出请求来源不被允许",
    },
    500: {
        code: "logout_failed",
        message: "登出失败，请稍候再试",
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
        "登出服务返回异常，请稍候再试",
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

function readLoginCookie(cookieHeader: string | null): LoginCookie {
    let found = false;
    let token: string | null = null;

    for (const segment of (cookieHeader ?? "").split(";")) {
        const pair = segment.trimStart();
        const separator = pair.indexOf("=");

        if (
            separator === -1 ||
            pair.slice(0, separator) !== LOGIN_COOKIE_NAME
        ) {
            continue;
        }

        if (found) {
            return { token: null, duplicate: true };
        }

        found = true;

        const value = pair.slice(separator + 1);

        if (/^[A-Za-z0-9_-]{43}$/.test(value)) {
            token = value;
        }
    }

    return { token, duplicate: false };
}

function isLoginCookieDeletion(cookie: string): boolean {
    const [pair, ...attributes] = cookie.split(";");

    if (!pair.startsWith(`${LOGIN_COOKIE_NAME}=`)) {
        return false;
    }

    const normalizedAttributes = attributes.map((attribute) =>
        attribute.trim().toLowerCase(),
    );

    return (
        normalizedAttributes.includes("max-age=0") &&
        normalizedAttributes.includes("path=/") &&
        !normalizedAttributes.some((attribute) =>
            attribute.startsWith("domain="),
        )
    );
}

export async function POST(request: Request): Promise<Response> {
    const origin = request.headers.get("origin");

    if (origin === null || !ALLOWED_ORIGINS.includes(origin)) {
        return errorResponse(
            403,
            "logout_origin_rejected",
            "登出请求来源不被允许",
        );
    }

    const loginCookie = readLoginCookie(
        request.headers.get("cookie"),
    );

    if (loginCookie.duplicate) {
        return errorResponse(
            400,
            "ambiguous_login_cookie",
            "登录 Cookie 存在冲突，无法完成登出",
        );
    }

    const headers = new Headers({ Origin: origin });

    if (loginCookie.token !== null) {
        headers.set(
            "Cookie",
            `${LOGIN_COOKIE_NAME}=${loginCookie.token}`,
        );
    }

    const timeoutSignal = AbortSignal.timeout(LOGOUT_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/auth/logout`,
            {
                method: "POST",
                headers,
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        if (backendResponse.status === 204) {
            const setCookies = backendResponse.headers.getSetCookie();

            if (!setCookies.some(isLoginCookieDeletion)) {
                return invalidBackendResponse();
            }

            const response = new Response(null, {
                status: 204,
                headers: { "Cache-Control": "no-store" },
            });

            for (const cookie of setCookies) {
                response.headers.append("Set-Cookie", cookie);
            }

            return response;
        }

        const payload: unknown = await backendResponse.json();

        if (!isRecord(payload)) {
            return invalidBackendResponse();
        }

        const safeError = BACKEND_ERRORS[backendResponse.status];

        if (!safeError || payload.code !== safeError.code) {
            return invalidBackendResponse();
        }

        return errorResponse(
            backendResponse.status,
            safeError.code,
            safeError.message,
        );
    } catch {
        if (request.signal.aborted) {
            return errorResponse(
                499,
                "logout_request_cancelled",
                "登出请求已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "logout_backend_timeout",
                "登出服务响应超时，请稍候再试",
            );
        }

        return errorResponse(
            502,
            "logout_backend_unavailable",
            "登出服务暂时不可用，请稍候再试",
        );
    }
}