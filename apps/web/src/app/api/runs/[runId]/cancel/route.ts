import { isLocalMode, localHeaders } from "../../../_shared/runtime.ts";

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
    400: "运行请求无法解析",
    401: "登录状态无效，请重新登录",
    403: "运行请求来源不被允许",
    404: "运行不存在或不可访问",
    415: "运行请求必须使用 application/json",
    422: "运行请求信息不符合要求",
    429: "请求太频繁，请稍后再试",
    500: "运行服务暂时出错",
    502: "后端服务暂时不可用",
    503: "取消服务暂时不可用",
    504: "取消服务响应超时",
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
        // 上游可能已断开，不读取或记录其错误正文。
    }
}

export async function POST(
    request: Request,
    context: RouteContext<"/api/runs/[runId]/cancel">,
): Promise<Response> {
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

    const { runId } = await context.params;

    if (!/^[1-9]\d*$/.test(runId)) {
        return errorResponse(422, SAFE_ERRORS[422]);
    }

    let requestBody: unknown;

    try {
        requestBody = await request.json();
    } catch {
        return errorResponse(400, SAFE_ERRORS[400]);
    }

    let backendResponse: Response;

    try {
        backendResponse = await fetch(
            `${API_BASE_URL}/runs/${encodeURIComponent(runId)}/cancel`,
            {
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
            },
        );
    } catch {
        if (request.signal.aborted) {
            return errorResponse(499, "取消请求已中断");
        }

        return errorResponse(502, SAFE_ERRORS[502]);
    }

    await discardBody(backendResponse);

    if (backendResponse.status === 204) {
        return new Response(null, {
            status: 204,
            headers: {
                "Cache-Control": "no-store",
            },
        });
    }

    const message = SAFE_ERRORS[backendResponse.status];

    if (message !== undefined) {
        return errorResponse(backendResponse.status, message);
    }

    return errorResponse(502, "取消服务返回异常");
}
