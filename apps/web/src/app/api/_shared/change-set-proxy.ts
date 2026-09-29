import { isLocalMode, localHeaders } from './runtime.ts';
import { isProposalIdentifier } from '../../../features/workbench/file-edit-proposal-data.ts';
import { parseChangeSet } from '../../../features/workbench/change-set-data.ts';
const error = (status: number) => Response.json({ code: 'change_set_unavailable', message: '未取得可信结果，请查询变更状态，勿重放应用。' }, { status, headers: { 'Cache-Control': 'no-store' } });
export async function changeSetProxy(request: Request, workspaceId: string, taskId: string, changeId?: string) {
    const url = new URL(request.url);
    if (request.method !== (changeId ? 'POST' : 'GET')) return error(405);
    if (changeId && request.headers.get('origin') !== url.origin) return error(403);
    if (![workspaceId, taskId, ...(changeId ? [changeId] : [])].every(isProposalIdentifier) || url.searchParams.size) return error(422);
    let headers: Record<string, string>, body: string | undefined;
    try { if (!isLocalMode()) return error(403); headers = localHeaders(request); } catch { return error(403); }
    if (changeId) {
        if (request.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase() !== 'application/json') return error(415);
        try {
            const raw = await request.json();
            if (!raw || Object.keys(raw).length !== 1 || !['approve', 'reject', 'apply', 'restore'].includes(raw.action)) return error(422);
            body = JSON.stringify({ action: raw.action });
        } catch { return error(422); }
    }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(25000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks/${taskId}/change-sets${changeId ? '/' + changeId : ''}`, {
            method: request.method, headers: { ...headers, ...(changeId ? { Origin: url.origin, 'Content-Type': 'application/json' } : {}) },
            body, cache: 'no-store', redirect: 'error', signal,
        });
        if (response.status !== 200 || response.headers.get('content-type')?.split(';', 1)[0] !== 'application/json') {
            await response.body?.cancel(); return error([401, 403, 404, 409, 422].includes(response.status) ? response.status : 502);
        }
        const raw = await response.json(); signal.throwIfAborted();
        if (!raw || raw.workspace_id !== workspaceId || raw.task_id !== taskId) return error(502);
        const base = { workspace_id: workspaceId, task_id: taskId };
        if (changeId) {
            const item = parseChangeSet(raw.item);
            if (!item || item.change_id !== changeId) return error(502);
            return Response.json({ ...base, item }, { headers: { 'Cache-Control': 'no-store' } });
        }
        if (!Array.isArray(raw.items) || raw.items.length > 50) return error(502);
        const items = raw.items.map(parseChangeSet);
        if (items.some((i: unknown) => !i)) return error(502);
        return Response.json({ ...base, items }, { headers: { 'Cache-Control': 'no-store' } });
    } catch { return error(request.signal.aborted ? 499 : 502); }
}
