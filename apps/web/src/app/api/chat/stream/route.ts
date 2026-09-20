import { isLocalMode, localHeaders } from "../../_shared/runtime.ts";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export const runtime = "nodejs";

const ALLOWED_ORIGINS = (
    process.env.AUTH_ALLOWED_ORIGINS ??
    "http://localhost:3000,http://127.0.0.1:3000"
)
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);

const SAFE_ERRORS: Record<number, string> = {
    400: "聊天请求无法解析",
    401: "登录状态无效，请重新登录",
    403: "聊天请求来源不被允许",
    404: "会话不存在或不可访问",
    409: "该会话仍在执行或收尾，请稍后再试",
    415: "聊天请求必须使用 application/json",
    422: "聊天信息不符合要求",
    429: "请求太频繁，请稍后再试",
    500: "聊天服务暂时出错",
    502: "模型服务暂时不可用",
    503: "执行服务暂时繁忙或不可用，请稍后再试",
    504: "聊天服务响应超时",
};

function errorResponse(status: number, message: string): Response {
    return new Response(message, {
        status,
        headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store",
        },
    });
}

function readLoginToken(cookieHeader: string | null): string | null {
    let token: string | null = null;

    for (const segment of (cookieHeader ?? "").split(";")) {
        const pair = segment.trimStart();
        const separator = pair.indexOf("=");

        if (
            separator === -1 ||
            pair.slice(0, separator) !== "agent_session"
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

async function discardBody(response: Response): Promise<void> {
    try {
        await response.body?.cancel();
    } catch {
        // 上游可能已经断开；不读取或记录错误正文。
    }
}

export async function POST(request: Request): Promise<Response> {
    const origin = request.headers.get("origin");

    if (origin === null || !ALLOWED_ORIGINS.includes(origin)) {
        return errorResponse(403, SAFE_ERRORS[403]);
    }

    const contentType = request.headers
        .get("content-type")
        ?.split(";")[0]
        .trim()
        .toLowerCase();

    if (contentType !== "application/json") {
        return errorResponse(415, SAFE_ERRORS[415]);
    }

    const token = readLoginToken(request.headers.get("cookie"));

    if (!isLocalMode() && token === null) {
        return errorResponse(401, SAFE_ERRORS[401]);
    }

    let requestBody: unknown;

    try {
        requestBody = await request.json();
    } catch {
        return errorResponse(400, SAFE_ERRORS[400]);
    }

    let backendResponse: Response;

    try {
        backendResponse = await fetch(`${API_BASE_URL}/chat/stream`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Origin: origin,
                ...(isLocalMode() ? localHeaders(request) : { Cookie: `agent_session=${token}` }),
            },
            body: JSON.stringify(requestBody),
            signal: request.signal,
            cache: "no-store",
            redirect: "error",
        });
    } catch {
        if (request.signal.aborted) {
            return errorResponse(499, "聊天请求已取消");
        }

        return errorResponse(502, "后端服务暂时不可用");
    }

    if (backendResponse.status !== 200) {
        await discardBody(backendResponse);

        const message = SAFE_ERRORS[backendResponse.status];

        if (message !== undefined) {
            return errorResponse(backendResponse.status, message);
        }

        return errorResponse(502, "聊天服务返回异常");
    }

    const backendContentType = backendResponse.headers
        .get("content-type")
        ?.split(";")[0]
        .trim()
        .toLowerCase();

    if (
        backendContentType !== "application/x-ndjson" ||
        backendResponse.body === null
    ) {
        await discardBody(backendResponse);
        return errorResponse(502, "聊天服务没有返回有效的流式内容");
    }

    const responseHeaders = new Headers({
        "Content-Type": "application/x-ndjson",
        "Cache-Control": "no-store",
    });

    const runId = backendResponse.headers.get("x-run-id");

    if (runId !== null) {
        if (!/^[1-9]\d*$/.test(runId)) {
            await discardBody(backendResponse);
            return errorResponse(502, "聊天服务返回异常");
        }

        responseHeaders.set("X-Run-ID", runId);
    }

    return new Response(backendResponse.body, {
        status: 200,
        headers: responseHeaders,
    });
}
