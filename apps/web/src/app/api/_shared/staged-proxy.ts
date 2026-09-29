import { isLocalMode, localHeaders } from "./runtime.ts";
import { MAX_STAGED_BYTES, readStagedData } from "../../../features/workbench/staged-data.ts";

const errors: Record<number, string[]> = {
    403: ["local_mode_required", "local_access_rejected", "workspace_origin_rejected"],
    404: ["workspace_not_accessible"], 409: ["staged_observation_changed"],
    422: ["invalid_staged_input", "invalid_workspace_input", "staged_observation_unsupported", "staged_observation_limit", "staged_response_limit"],
    500: ["staged_read_failed"],
};
function error(status: number, code = "staged_read_failed") {
    return Response.json({ code, message: "未取得可确认的暂存差异，请检查项目状态后重试" }, {
        status, headers: { "Cache-Control": "no-store" },
    });
}
// 流式计费并让取消覆盖正文阶段，不能先response.json再判断大小。
export async function boundedJson(response: Response, signal: AbortSignal): Promise<unknown> {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing body");
    const chunks: Uint8Array[] = [];
    let size = 0;
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener("abort", cancel, { once: true });
    try {
        while (true) {
            signal.throwIfAborted();
            const { done, value } = await reader.read();
            signal.throwIfAborted();
            if (done) break;
            size += value.byteLength;
            if (size > MAX_STAGED_BYTES) throw new Error("limit");
            chunks.push(value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } finally {
        signal.removeEventListener("abort", cancel);
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}
export async function stagedProxy(request: Request, workspaceId: string, taskId: string): Promise<Response> {
    if (request.method !== "GET") return error(405);
    const url = new URL(request.url);
    const origin = request.headers.get("origin");
    if (origin && origin !== url.origin) return error(403, "workspace_origin_rejected");
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return error(403, "local_mode_required");
        headers = localHeaders(request);
    } catch { return error(403, "local_access_rejected"); }
    const entries = [...url.searchParams];
    const rawRevision = entries[0]?.[1] ?? "";
    const revision = Number(rawRevision);
    if (![workspaceId, taskId].every(v => /^[0-9a-f]{32}$/.test(v)) || entries.length !== 1
        || entries[0][0] !== "binding_revision" || !/^[1-9][0-9]{0,15}$/.test(rawRevision)
        || !Number.isSafeInteger(revision)) return error(422, "invalid_staged_input");
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/workspaces/${workspaceId}/tasks/${taskId}/git/staged?binding_revision=${revision}`, {
            headers: { ...headers, ...(origin ? { Origin: origin } : {}) }, cache: "no-store", redirect: "error", signal,
        });
        if (response.headers.get("content-type")?.split(";", 1)[0] !== "application/json"
            || (response.status !== 200 && !errors[response.status])) {
            await response.body?.cancel();
            throw new Error("invalid response");
        }
        const raw = await boundedJson(response, signal);
        signal.throwIfAborted();
        if (response.status !== 200) {
            if (raw && typeof raw === "object" && "code" in raw && typeof raw.code === "string"
                && errors[response.status].includes(raw.code)) return error(response.status, raw.code);
            throw new Error("invalid error");
        }
        const data = readStagedData(raw, workspaceId, taskId, revision);
        if (!data) throw new Error("invalid receipt");
        return Response.json(data, { headers: { "Cache-Control": "no-store" } });
    } catch { return error(request.signal.aborted ? 499 : timeout.aborted ? 504 : 502); }
}


export async function stagedBindingProxy(request: Request, workspaceId: string, taskId: string): Promise<Response> {
    const url = new URL(request.url);
    const origin = request.headers.get("origin");
    if (request.method !== "GET") return error(405);
    if (origin && origin !== url.origin) return error(403, "workspace_origin_rejected");
    if (url.search || ![workspaceId, taskId].every(v => /^[0-9a-f]{32}$/.test(v))) return error(422, "invalid_staged_input");
    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);
    try {
        if (!isLocalMode()) return error(403, "local_mode_required");
        const headers = localHeaders(request);
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/workspaces/${workspaceId}/tasks/${taskId}/git/binding`, {
            headers: { ...headers, ...(origin ? { Origin: origin } : {}) }, signal, redirect: "error", cache: "no-store",
        });
        if (response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
            await response.body?.cancel(); throw new Error("type");
        }
        const raw = await boundedJson(response, signal);
        if (response.status !== 200) {
            if (raw && typeof raw === "object" && "code" in raw && typeof raw.code === "string" && errors[response.status]?.includes(raw.code)) return error(response.status, raw.code);
            throw new Error("status");
        }
        if (!raw || typeof raw !== "object" || !("workspace_id" in raw) || raw.workspace_id !== workspaceId
            || !("task_id" in raw) || raw.task_id !== taskId || !("binding_revision" in raw)
            || typeof raw.binding_revision !== "number" || !Number.isSafeInteger(raw.binding_revision) || raw.binding_revision < 1
            || !("bound" in raw) || typeof raw.bound !== "boolean") throw new Error("receipt");
        return Response.json({ workspace_id: workspaceId, task_id: taskId, binding_revision: raw.binding_revision, bound: raw.bound }, { headers: { "Cache-Control": "no-store" } });
    } catch { return error(request.signal.aborted ? 499 : timeout.aborted ? 504 : 502); }
}
