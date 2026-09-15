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

const REGISTER_TIMEOUT_MS = 10_000;

type SafeError = {
    code: string;
    message: string;
};

const BACKEND_ERRORS: Record<number, SafeError> = {
    400: {
        code: "request_rejected",
        message: "请求无法处理",
    },
    409: {
        code: "username_already_exists",
        message: "用户名已被使用",
    },
    403: {
        code: "registration_origin_rejected",
        message: "注册请求来源不被允许",
    },
    415: {
        code: "unsupported_registration_content_type",
        message: "注册请求必须使用 application/json",
    },
    422: {
        code: "invalid_registration_input",
        message: "注册信息不符合要求，请检查用户名和密码",
    },
    500: {
        code: "registration_failed",
        message: "注册失败，请稍候再试",
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
        "注册服务返回异常，请稍候再试",
    );
}

export async function POST(request: Request): Promise<Response> {
    if (isLocalMode()) return Response.json({ code: "local_account_disabled", message: "本地模式无需账号登录" }, { status: 403, headers: { "Cache-Control": "no-store" } });
    const origin = request.headers.get("origin");

    if (origin === null || !ALLOWED_ORIGINS.includes(origin)) {
        return errorResponse(
            403,
            "registration_origin_rejected",
            "注册请求来源不被允许",
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
            "unsupported_registration_content_type",
            "注册请求必须使用 application/json",
        );
    }

    let requestBody: unknown;

    try {
        requestBody = await request.json();
    }
    catch {
        return errorResponse(
            400,
            "invalid_registration_json",
            "注册请求不是有效的 JSON",
        );
    }

    // 只接受凭证字段，规范化与密码规则由后端统一执行。
    if (
        !isRecord(requestBody) ||
        Object.keys(requestBody).length !== 2 ||
        typeof requestBody.username !== "string" ||
        typeof requestBody.password !== "string"
    ) {
        return errorResponse(422, "invalid_registration_input", "注册信息不符合要求，请检查用户名和密码");
    }

    const timeoutSignal = AbortSignal.timeout(REGISTER_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeoutSignal]);

    try {
        const backendResponse = await fetch(
            `${API_BASE_URL}/auth/register`,
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

        if (backendResponse.status !== 201) {
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

        // 注册不签发登录会话，不转发后端 Set-Cookie。
        const response = Response.json(
            {
                external_id: payload.external_id,
                username: payload.username,
            },
            {
                status: 201,
                headers: { "Cache-Control": "no-store" },
            },
        );

        return response;
    }
    catch {
        if (request.signal.aborted) {
            return errorResponse(
                499,
                "registration_request_cancelled",
                "注册请求已取消",
            );
        }

        if (timeoutSignal.aborted) {
            return errorResponse(
                504,
                "registration_backend_timeout",
                "注册服务响应超时，结果尚未确认，可尝试登录",
            );
        }

        return errorResponse(
            502,
            "registration_backend_unavailable",
            "注册结果尚未确认，可稍后尝试登录",
        );
    }
}
