import { isLocalMode, localHeaders } from "./runtime.ts";
import { readSettings } from "../../../features/model-settings/data.ts";

async function bounded(body: ReadableStream<Uint8Array> | null, signal: AbortSignal): Promise<string> {
    if (!body) throw new Error();
    const reader = body.getReader(), chunks: Uint8Array[] = [];
    let size = 0;
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener("abort", cancel, { once: true });
    try {
        signal.throwIfAborted();
        for (;;) {
            const item = await reader.read();
            signal.throwIfAborted();
            if (item.done) break;
            size += item.value.byteLength;
            if (size > 16384) throw new Error();
            chunks.push(item.value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } finally {
        signal.removeEventListener("abort", cancel);
        void reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

export async function modelSettingsProxy(request: Request, detect = false): Promise<Response> {
    const reply = (data: unknown, status = 200) => Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
    const fail = (status: number) => reply({ message: status === 409 ? "配置已变化，请重新打开设置" : "配置操作失败，请检查填写内容后重试" }, status);
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return fail(403);
        headers = localHeaders(request);
        if (request.method !== "GET") {
            const origin = request.headers.get("origin");
            if (!origin || origin !== new URL(request.url).origin) return fail(403);
            if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return fail(415);
            headers = { ...headers, Origin: origin, "Content-Type": "application/json" };
        }
    } catch { return fail(403); }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20000)]);
    try {
        const body = request.method === "GET" ? undefined : await bounded(request.body, signal);
        if (body !== undefined) JSON.parse(body);
        const response = await fetch(
            (process.env.API_BASE_URL ?? "http://127.0.0.1:8000") + "/model-settings" + (detect ? "/detect-dimensions" : ""),
            { method: request.method, headers, body, signal, redirect: "error", cache: "no-store" },
        );
        const text = await bounded(response.body, signal);
        if (!response.ok) return fail([403, 409, 413, 415, 422].includes(response.status) ? response.status : 502);
        const raw: unknown = JSON.parse(text);
        if (detect) {
            const value = raw as { dimensions?: unknown } | null;
            if (!value || Object.keys(value).join() !== "dimensions" || typeof value.dimensions !== "number"
                || !Number.isInteger(value.dimensions) || value.dimensions < 1 || value.dimensions > 4096) return fail(502);
            return reply({ dimensions: value.dimensions });
        }
        const parsed = readSettings(raw);
        return parsed ? reply(parsed) : fail(502);
    } catch { return fail(502); }
}
