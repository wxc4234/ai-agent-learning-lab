import {
    isCodeContextIdentifier, parseCodeContextJson, readCodeQueryContextRequest, readCodeQueryContext,
    type CodeQueryContext,
} from "./code-query-context-data.ts";
import type { CodeBatchSummary } from "./code-batch-summaries-data.ts";

export const CODE_QUERY_BROWSER_BYTES = 1024 * 1024;
// 略长于BFF的70秒预算；等待覆盖fetch、正文和异步摘要校验。
export const CODE_QUERY_BROWSER_TIMEOUT_MS = 75_000;

function cancel(body: ReadableStream<Uint8Array> | null) {
    void body?.cancel().catch(() => {});
}

export async function previewCodeQueryContext(
    workspaceId: string, taskId: string, batch: CodeBatchSummary, query: string, signal: AbortSignal,
): Promise<CodeQueryContext> {
    const request = readCodeQueryContextRequest({ query, batch_id: batch.batch_id, response_model: batch.response_model });
    if (![workspaceId, taskId].every(isCodeContextIdentifier) || !request) throw new Error("invalid_code_query_input");
    signal.throwIfAborted();
    // 只发布三个公开输入；连接、凭证、Top K和预算留在服务端，没有浏览器事务。
    const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/code-query-context`, {
        method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal,
    });
    if (signal.aborted || response.status !== 200
        || response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
        cancel(response.body);
        signal.throwIfAborted();
        // 失败不读取/展示供应商或内部错误正文，也不能投影为合法空结果。
        throw new Error("code_query_response_failed");
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing_code_query_body");
    const abort = () => { void reader.cancel().catch(() => {}); };
    const pieces: Uint8Array[] = [];
    let size = 0;
    signal.addEventListener("abort", abort, { once: true });
    try {
        // fetch已自动解压；按实际正文计量，不信任Content-Length或编码声明。
        while (true) {
            signal.throwIfAborted();
            const { value, done } = await reader.read();
            signal.throwIfAborted();
            if (done) break;
            size += value.byteLength;
            if (size > CODE_QUERY_BROWSER_BYTES) throw new Error("code_query_body_limit");
            pieces.push(value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const piece of pieces) { bytes.set(piece, offset); offset += piece.byteLength; }
        const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
        const result = await readCodeQueryContext(parseCodeContextJson(text), workspaceId, taskId, request);
        signal.throwIfAborted();
        if (!result) throw new Error("invalid_code_query_snapshot");
        const context = result.context, metadata = context.source_metadata;
        // 请求中的报告版本不足以证明是用户所选空间；重新核对摘要里的不可变事实。
        if (context.space_id !== batch.space_id || context.dimensions !== batch.dimensions
            || result.requested_model !== batch.requested_model || context.recall_summary.batch_chunk_count !== batch.chunk_count
            || metadata.truncated !== batch.truncated
            || metadata.incomplete_reasons.length !== batch.incomplete_reasons.length
            || !metadata.incomplete_reasons.every(reason => batch.incomplete_reasons.includes(reason))) {
            throw new Error("code_query_batch_mismatch");
        }
        return result;
    } finally {
        signal.removeEventListener("abort", abort);
        // 不等待任意cancel回调，仍释放reader锁。
        abort();
        reader.releaseLock();
    }
}
