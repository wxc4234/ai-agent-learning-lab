import { isCodeContextIdentifier, parseCodeContextJson } from "./code-query-context-data.ts";
import { readCodeBatchSummaries, type CodeBatchSummaries } from "./code-batch-summaries-data.ts";

export const CODE_BATCH_BROWSER_BYTES = 64 * 1024;
export const CODE_BATCH_BROWSER_TIMEOUT_MS = 25_000;

function cancel(body: ReadableStream<Uint8Array> | null) {
    // 不等待传输适配器的任意cancel回调；无用正文不能拖住已知失败。
    void body?.cancel().catch(() => {});
}

export async function loadCodeBatchSummaries(
    workspaceId: string,
    taskId: string,
    signal: AbortSignal,
): Promise<CodeBatchSummaries> {
    if (![workspaceId, taskId].every(isCodeContextIdentifier)) throw new Error("invalid_code_batch_scope");
    signal.throwIfAborted();
    // 浏览器只访问同源BFF，没有内部token、模型配置或数据库事务。
    const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/code-embedding-batches`, {
        method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
    if (signal.aborted || response.status !== 200
        || response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
        cancel(response.body);
        signal.throwIfAborted();
        throw new Error("code_batch_response_failed");
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing_code_batch_body");
    const abort = () => { void reader.cancel().catch(() => {}); };
    const pieces: Uint8Array[] = [];
    let size = 0;
    signal.addEventListener("abort", abort, { once: true });
    try {
        // 按浏览器解压后实际读取的字节限量；声明长度不代表完整正文大小。
        while (true) {
            signal.throwIfAborted();
            const { value, done } = await reader.read();
            signal.throwIfAborted();
            if (done) break;
            size += value.byteLength;
            if (size > CODE_BATCH_BROWSER_BYTES) throw new Error("code_batch_body_limit");
            pieces.push(value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const piece of pieces) { bytes.set(piece, offset); offset += piece.byteLength; }
        const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
        const snapshot = readCodeBatchSummaries(parseCodeContextJson(text), workspaceId, taskId);
        signal.throwIfAborted();
        if (!snapshot) throw new Error("invalid_code_batch_snapshot");
        return snapshot;
    } finally {
        signal.removeEventListener("abort", abort);
        abort();
        reader.releaseLock();
    }
}
