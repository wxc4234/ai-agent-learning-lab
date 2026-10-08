import { parseCodeContextJson } from "../../../features/workbench/code-query-context-data.ts";

export async function readCodeContextJson(
    body: ReadableStream<Uint8Array> | null,
    signal: AbortSignal,
    maximum: number,
): Promise<unknown> {
    signal.throwIfAborted();
    const reader = body?.getReader();
    if (!reader) throw new Error("missing_code_context_body");
    const pieces: Uint8Array[] = [];
    let size = 0;
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener("abort", cancel, { once: true });
    try {
        while (true) {
            signal.throwIfAborted();
            const { value, done } = await reader.read();
            signal.throwIfAborted();
            if (done) break;
            size += value.byteLength;
            if (size > maximum) throw new Error("code_context_body_limit");
            pieces.push(value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const piece of pieces) { bytes.set(piece, offset); offset += piece.byteLength; }
        // fatal拒绝非法UTF-8；ignoreBOM保留BOM，由JSON语法拒绝它。
        const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
        const result = parseCodeContextJson(text);
        signal.throwIfAborted();
        return result;
    } finally {
        signal.removeEventListener("abort", cancel);
        // 不等待任意流cancel回调；它不能使已取消的请求无限挂起。
        cancel();
        reader.releaseLock();
    }
}
