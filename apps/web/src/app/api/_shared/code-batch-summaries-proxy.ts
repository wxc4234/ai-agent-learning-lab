import { isLocalMode, localHeaders } from "./runtime.ts";
import { readCodeContextJson } from "./code-query-context-json.ts";
import { isCodeContextIdentifier } from "../../../features/workbench/code-query-context-data.ts";
import { readCodeBatchSummaries } from "../../../features/workbench/code-batch-summaries-data.ts";

export const CODE_BATCH_RESPONSE_BYTES = 64 * 1024;
export const CODE_BATCH_ERROR_BYTES = 4 * 1024;
export const CODE_BATCH_TIMEOUT_MS = 20_000;
const failed = "code_embedding_batch_list_failed";
const invalid = "invalid_code_embedding_batch_list_input";
const messages: Record<string, string> = {
    [failed]: "代码向量批次读取失败，结果未知",
    [invalid]: "代码向量批次查询参数不符合要求",
    code_embedding_batch_list_method_not_allowed: "代码向量批次查询只支持GET",
    local_mode_required: "任务功能仅支持本地模式",
    local_access_rejected: "本地服务请求被拒绝，请检查运行配置",
    workspace_origin_rejected: "请求来源不被允许",
    invalid_login_session: "身份无效，请检查本地运行配置",
    workspace_not_accessible: "工作空间不存在或不可访问",
    code_embedding_project_unbound: "请先绑定项目目录",
};
const errors: Partial<Record<number, readonly string[]>> = {
    401: ["invalid_login_session"],
    403: ["local_mode_required", "local_access_rejected", "workspace_origin_rejected"],
    404: ["workspace_not_accessible"],
    409: ["code_embedding_project_unbound"],
    422: [invalid],
    500: [failed],
};

function error(status: number, code = failed): Response {
    return Response.json({ code, message: messages[code] }, { status, headers: { "Cache-Control": "no-store" } });
}
function cancel(body: ReadableStream<Uint8Array> | null) {
    // 无用响应/不支持正文不再读取，不等待任意cancel回调，也不改变已知错误。
    void body?.cancel().catch(() => {});
}

export async function codeBatchSummariesProxy(request: Request, workspaceId: string, taskId: string): Promise<Response> {
    if (request.method !== "GET") { cancel(request.body); return error(405, "code_embedding_batch_list_method_not_allowed"); }
    const url = new URL(request.url);
    const origin = request.headers.get("origin");
    // 浏览器同源GET可省略Origin；有声明时必须匹配当前来源。
    if (origin !== null && origin !== url.origin) { cancel(request.body); return error(403, "workspace_origin_rejected"); }
    let headers: Record<string, string>;
    let backend: URL;
    try {
        if (!isLocalMode()) { cancel(request.body); return error(403, "local_mode_required"); }
        headers = localHeaders(request);
        backend = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
        const allowed = (process.env.AUTH_ALLOWED_ORIGINS ?? "http://localhost:3000,http://127.0.0.1:3000").split(",").map(value => value.trim());
        // 不把本机凭证交给带URI凭证/额外参数的地址；缺省Origin也要经过精确允许列表。
        if (!allowed.includes(url.origin) || backend.username || backend.password || backend.search || backend.hash) throw new Error("invalid_backend_or_origin");
    } catch { cancel(request.body); return error(403, "local_access_rejected"); }
    const contentLength = request.headers.get("content-length");
    if (!isCodeContextIdentifier(workspaceId) || !isCodeContextIdentifier(taskId) || url.searchParams.size
        || request.body !== null || (contentLength !== null && contentLength !== "0") || request.headers.has("transfer-encoding")) {
        cancel(request.body);
        return error(422, invalid);
    }
    const timeout = AbortSignal.timeout(CODE_BATCH_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeout]);
    const transportStatus = () => request.signal.aborted ? 499 : timeout.aborted ? 504 : 502;
    try {
        signal.throwIfAborted();
        backend.pathname = backend.pathname.replace(/\/$/, "") + `/workspaces/${workspaceId}/tasks/${taskId}/code-embedding-batches`;
        // 只GET已存摘要；没有数据库事务、扫描/生成/保存或模型请求，也不自动选批次。
        const response = await fetch(backend, {
            method: "GET", headers: { ...headers, Origin: url.origin, "Accept-Encoding": "identity" },
            signal, cache: "no-store", redirect: "error",
        });
        if (signal.aborted) { cancel(response.body); signal.throwIfAborted(); }
        const allowed = errors[response.status];
        if ((response.status !== 200 && !allowed)
            || response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json"
            || (response.headers.get("content-encoding") ?? "identity").toLowerCase() !== "identity") {
            cancel(response.body);
            return error(502);
        }
        const raw = await readCodeContextJson(response.body, signal,
            response.status === 200 ? CODE_BATCH_RESPONSE_BYTES : CODE_BATCH_ERROR_BYTES);
        signal.throwIfAborted();
        if (response.status !== 200) {
            if (raw && typeof raw === "object" && !Array.isArray(raw) && "code" in raw
                && typeof raw.code === "string" && allowed?.includes(raw.code)) return error(response.status, raw.code);
            return error(502);
        }
        const publicResult = readCodeBatchSummaries(raw, workspaceId, taskId);
        signal.throwIfAborted();
        if (!publicResult) return error(502);
        return Response.json(publicResult, { headers: { "Cache-Control": "no-store" } });
    } catch { return error(transportStatus()); }
}
