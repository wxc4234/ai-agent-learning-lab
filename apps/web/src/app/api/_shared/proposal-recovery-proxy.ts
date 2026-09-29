import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";

const EVENTS = new Set(["created", "approved", "rejected", "grant_issued", "grant_revoked", "application_started", "applied", "not_applied", "uncertain", "restore_requested"]);
const error = (status: number) => Response.json({ code: "proposal_recovery_unavailable", message: "未取得可信结果，请查询记录后再处理。" }, { status, headers: { "Cache-Control": "no-store" } });
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

// 读审计与生成反向提案共享资源校验；固定路径和公开投影，不接收文件正文或原许可。
export async function proposalRecoveryProxy(request: Request, workspaceId: string, taskId: string, proposalId: string, restore: boolean) {
    const url = new URL(request.url);
    if (request.method !== (restore ? "POST" : "GET")) return error(405);
    if (restore && request.headers.get("origin") !== url.origin) return error(403);
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return error(403);
        headers = localHeaders(request);
    } catch { return error(403); }
    if (![workspaceId, taskId, proposalId].every(isProposalIdentifier) || url.searchParams.size) return error(422);
    if (restore) {
        if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return error(415);
        try {
            const body: unknown = await request.json();
            if (!record(body) || Object.keys(body).length !== 1 || body.action !== "restore") return error(422);
        } catch { return error(422); }
    }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/${restore ? "restore-proposal" : "audit"}`, {
            method: restore ? "POST" : "GET", headers: { ...headers, ...(restore ? { Origin: url.origin, "Content-Type": "application/json" } : {}) },
            ...(restore ? { body: JSON.stringify({ action: "restore" }) } : {}), signal, cache: "no-store", redirect: "error",
        });
        signal.throwIfAborted();
        if (response.status !== 200 || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
            await response.body?.cancel();
            return error([401, 403, 404, 409, 422].includes(response.status) ? response.status : 502);
        }
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        if (!record(raw) || raw.workspace_id !== workspaceId || raw.task_id !== taskId || raw.proposal_id !== proposalId) return error(502);
        const base = { workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId };
        if (restore) {
            if (typeof raw.restore_proposal_id !== "string" || !isProposalIdentifier(raw.restore_proposal_id)) return error(502);
            return Response.json({ ...base, restore_proposal_id: raw.restore_proposal_id }, { headers: { "Cache-Control": "no-store" } });
        }
        if (!Array.isArray(raw.events) || raw.events.length > 100) return error(502);
        const events = [];
        for (const event of raw.events) {
            if (!record(event) || typeof event.event !== "string" || !EVENTS.has(event.event)
                || typeof event.created_at !== "string" || event.created_at.length > 50 || !Number.isFinite(Date.parse(event.created_at))
                || (event.event === "restore_requested" ? typeof event.restore_proposal_id !== "string" || !isProposalIdentifier(event.restore_proposal_id) : event.restore_proposal_id !== null)) return error(502);
            events.push({ event: event.event, created_at: event.created_at, restore_proposal_id: event.restore_proposal_id });
        }
        return Response.json({ ...base, events }, { headers: { "Cache-Control": "no-store" } });
    } catch { return error(request.signal.aborted ? 499 : 502); }
}
