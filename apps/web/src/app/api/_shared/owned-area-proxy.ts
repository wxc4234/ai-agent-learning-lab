import { isLocalMode, localHeaders } from './runtime.ts';
import { isProposalIdentifier } from '../../../features/workbench/file-edit-proposal-data.ts';
import { parseOwnedArea } from '../../../features/workbench/owned-area-data.ts';
const error = (status: number) => Response.json({ code: 'owned_area_unavailable', message: '操作结果未确认，请查询隔离工作区记录。' }, { status, headers: { 'Cache-Control': 'no-store' } });
export async function ownedAreaProxy(request: Request, workspaceId: string, taskId: string) {
    const url = new URL(request.url), write = request.method === 'POST';
    if (!['GET', 'POST'].includes(request.method)) return error(405);
    if (write && request.headers.get('origin') !== url.origin) return error(403);
    if (![workspaceId, taskId].every(isProposalIdentifier) || url.searchParams.size) return error(422);
    let headers: Record<string, string>, action: string | undefined;
    try { if (!isLocalMode()) return error(403); headers = localHeaders(request); } catch { return error(403); }
    if (write) {
        if (request.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase() !== 'application/json') return error(415);
        try {
            const raw = await request.json();
            if (!raw || Object.keys(raw).length !== 1 || !['create', 'export'].includes(raw.action)) return error(422);
            action = raw.action;
        } catch { return error(422); }
    }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(25000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks/${taskId}/owned-area`, {
            method: request.method, headers: { ...headers, ...(write ? { Origin: url.origin, 'Content-Type': 'application/json' } : {}) },
            ...(write ? { body: JSON.stringify({ action }) } : {}), signal, cache: 'no-store', redirect: 'error',
        });
        if (response.status !== 200 || response.headers.get('content-type')?.split(';', 1)[0] !== 'application/json') {
            await response.body?.cancel(); return error([401, 403, 404, 409, 422].includes(response.status) ? response.status : 502);
        }
        const raw = await response.json(); signal.throwIfAborted();
        if (raw?.workspace_id !== workspaceId || raw?.task_id !== taskId) return error(502);
        const base = { workspace_id: workspaceId, task_id: taskId };
        if (write) {
            const area = parseOwnedArea(raw.area);
            if (!area || (action === 'create' ? area.source_workspace_id !== workspaceId || area.source_task_id !== taskId
                : area.workspace_id !== workspaceId || area.task_id !== taskId || area.exported_change_id === null)) return error(502);
            return Response.json({ ...base, area }, { headers: { 'Cache-Control': 'no-store' } });
        }
        const current = raw.current === null ? null : parseOwnedArea(raw.current);
        if ((raw.current !== null && !current) || (current && (current.workspace_id !== workspaceId || current.task_id !== taskId))
            || !Array.isArray(raw.copies) || raw.copies.length > 20) return error(502);
        const copies = raw.copies.map(parseOwnedArea);
        if (copies.some((area: ReturnType<typeof parseOwnedArea>) => !area || area.source_workspace_id !== workspaceId || area.source_task_id !== taskId)) return error(502);
        return Response.json({ ...base, current, copies }, { headers: { 'Cache-Control': 'no-store' } });
    } catch { return error(request.signal.aborted ? 499 : 502); }
}
