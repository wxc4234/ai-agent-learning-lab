// 仅复制到临时Next站点；生产代理不导入此模块。
import { appendFileSync } from "node:fs";

export async function isolatedTraceFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const directory = process.env.BROWSER_TRACE_DIRECTORY;
    if (!directory) throw new Error("trace directory required");
    const url = new URL(input instanceof Request ? input.url : String(input));
    const event: Record<string, unknown> = {
        id: new Headers(init?.headers).get("x-isolated-trace-id"),
        path: url.pathname, method: init?.method ?? "GET",
    };
    try {
        const response = await fetch(input, init);
        event.status = response.status;
        event.backend_run = response.headers.get("x-isolated-run");
        event.content_type = response.headers.get("content-type");
        // 只保存短JSON的错误码，不保存原始正文或凭证，不消费调用方正文。
        try {
            const raw: unknown = await response.clone().json();
            if (raw && typeof raw === "object" && "code" in raw) event.code = raw.code;
        } catch { event.code = null; }
        return response;
    } catch (error) {
        event.transport_error = true;
        throw error;
    } finally {
        appendFileSync(`${directory}/bff.jsonl`, JSON.stringify(event) + "\n");
    }
}
