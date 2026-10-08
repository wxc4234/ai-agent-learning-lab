import { isLocalMode, localHeaders } from "./runtime.ts";
import { readCodeContextJson } from "./code-query-context-json.ts";
import {
    isCodeContextIdentifier, readCodeQueryContextRequest, readCodeQueryContext,
} from "../../../features/workbench/code-query-context-data.ts";

export const CODE_CONTEXT_REQUEST_BYTES = 16 * 1024;
export const CODE_CONTEXT_RESPONSE_BYTES = 1024 * 1024;
export const CODE_CONTEXT_ERROR_BYTES = 4 * 1024;
export const CODE_CONTEXT_TIMEOUT_MS = 70_000;
const failed = "code_query_context_failed";
const invalid = "invalid_code_query_context_input";

// 约束状态与code的组合；上游message和未知错误码永不透传。
const messages: Record<string, string> = {
    [failed]: "代码上下文查询失败，结果未知",
    [invalid]: "代码上下文查询参数不符合要求",
    code_query_context_method_not_allowed: "代码上下文查询只支持POST",
    local_mode_required: "任务功能仅支持本地模式",
    local_access_rejected: "本地服务请求被拒绝，请检查运行配置",
    workspace_origin_rejected: "请求来源不被允许",
    unsupported_workspace_content_type: "代码上下文查询必须使用application/json",
    invalid_login_session: "身份无效，请检查本地运行配置",
    workspace_not_accessible: "工作空间不存在或不可访问",
    embedding_not_configured: "请先配置独立Embedding地址、模型、密钥与维度",
    embedding_config_invalid: "Embedding配置无效",
    embedding_query_invalid: "查询文本不符合要求",
    embedding_query_budget_exceeded: "查询文本超过字符或字节预算",
    embedding_timeout: "Embedding请求超时，未返回代码上下文",
    embedding_request_failed: "Embedding请求失败，未返回代码上下文",
    embedding_response_invalid: "Embedding响应无效，未返回代码上下文",
    embedding_response_too_large: "Embedding响应超过读取预算",
    code_embedding_query_invalid: "查询或模型版本不符合要求",
    code_embedding_query_zero: "查询向量无法用于余弦召回",
    code_embedding_query_result_invalid: "查询结果与指定模型空间不一致",
    code_embedding_batch_inconsistent: "指定批次来源不一致",
    code_embedding_distance_invalid: "代码召回距离无效",
    code_context_budget_invalid: "服务端上下文预算无效",
    code_context_budget_too_small: "当前预算无法容纳批次来源声明",
    code_context_query_result_invalid: "查询上下文来源不一致",
    code_context_snapshot_invalid: "代码上下文快照无效",
    code_context_snapshot_too_large: "代码上下文快照超过构建预算",
};
const errors: Partial<Record<number, readonly string[]>> = {
    401: ["invalid_login_session"],
    403: ["local_mode_required", "local_access_rejected", "workspace_origin_rejected"],
    404: ["workspace_not_accessible"],
    409: ["code_embedding_query_result_invalid", "code_embedding_batch_inconsistent", "code_context_budget_too_small"],
    415: ["unsupported_workspace_content_type"],
    422: [invalid, "embedding_query_invalid", "embedding_query_budget_exceeded", "code_embedding_query_invalid"],
    500: [failed, "code_embedding_distance_invalid", "code_context_budget_invalid", "code_context_query_result_invalid", "code_context_snapshot_invalid", "code_context_snapshot_too_large"],
    502: ["embedding_request_failed", "embedding_response_invalid", "embedding_response_too_large", "code_embedding_query_zero"],
    503: ["embedding_not_configured", "embedding_config_invalid"],
    504: ["embedding_timeout"],
};

function error(status: number, code = failed): Response {
    return Response.json({ code, message: messages[code] }, { status, headers: { "Cache-Control": "no-store" } });
}
function cancel(body: ReadableStream<Uint8Array> | null) {
    // 释放不用的响应；取消结果不改变已确定的失败分类，也不无限等待回调。
    void body?.cancel().catch(() => {});
}

export async function codeQueryContextProxy(request: Request, workspaceId: string, taskId: string): Promise<Response> {
    if (request.method !== "POST") return error(405, "code_query_context_method_not_allowed");
    const url = new URL(request.url);
    const origin = request.headers.get("origin");
    if (!origin || origin !== url.origin) return error(403, "workspace_origin_rejected");
    let headers: Record<string, string>;
    let backend: URL;
    try {
        if (!isLocalMode()) return error(403, "local_mode_required");
        headers = localHeaders(request);
        backend = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
        // 复用本机门禁，再拒绝配置中的URI凭证/查询/片段，不把令牌交给其他地址。
        if (backend.username || backend.password || backend.search || backend.hash) throw new Error("invalid_backend");
    } catch { return error(403, "local_access_rejected"); }
    if (!isCodeContextIdentifier(workspaceId) || !isCodeContextIdentifier(taskId) || url.searchParams.size) return error(422, invalid);
    if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
        return error(415, "unsupported_workspace_content_type");
    }
    const timeout = AbortSignal.timeout(CODE_CONTEXT_TIMEOUT_MS);
    const signal = AbortSignal.any([request.signal, timeout]);
    const transportStatus = () => request.signal.aborted ? 499 : timeout.aborted ? 504 : 502;
    let body;
    try {
        body = readCodeQueryContextRequest(await readCodeContextJson(request.body, signal, CODE_CONTEXT_REQUEST_BYTES));
        signal.throwIfAborted();
    } catch {
        return error(signal.aborted ? transportStatus() : 422, signal.aborted ? failed : invalid);
    }
    if (!body) return error(422, invalid);
    try {
        signal.throwIfAborted();
        backend.pathname = backend.pathname.replace(/\/$/, "") + `/workspaces/${workspaceId}/tasks/${taskId}/code-query-context`;
        // 只有一次显式查询；BFF没有数据库事务，也不调用扫描/保存/Agent接口。
        const response = await fetch(backend, {
            method: "POST", headers: { ...headers, Origin: origin, "Content-Type": "application/json", "Accept-Encoding": "identity" },
            body: JSON.stringify(body), signal, cache: "no-store", redirect: "error",
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
            response.status === 200 ? CODE_CONTEXT_RESPONSE_BYTES : CODE_CONTEXT_ERROR_BYTES);
        signal.throwIfAborted();
        if (response.status !== 200) {
            if (raw && typeof raw === "object" && !Array.isArray(raw) && "code" in raw
                && typeof raw.code === "string" && allowed?.includes(raw.code)) return error(response.status, raw.code);
            return error(502);
        }
        const publicResult = await readCodeQueryContext(raw, workspaceId, taskId, body);
        signal.throwIfAborted();
        if (!publicResult) return error(502);
        // 不复制Set-Cookie、供应商/缓存头；只发布完整验证后的独立公开对象。
        return Response.json(publicResult, { headers: { "Cache-Control": "no-store" } });
    } catch { return error(transportStatus()); }
}
