import { isLocalMode, localHeaders } from './runtime.ts';
import { isProposalIdentifier } from '../../../features/workbench/file-edit-proposal-data.ts';
import { readTaskChanges } from '../../../features/workbench/task-changes-data.ts';

export async function taskChangesProxy(request: Request, workspaceId: string, taskId: string): Promise<Response> {
    const failure = (status: number) => Response.json({ message: '任务改动暂时不可用' }, { status, headers: { 'Cache-Control': 'no-store' } });
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return failure(403);
        headers = localHeaders(request);
    } catch { return failure(403); }
    const params = new URL(request.url).searchParams;
    const before = params.get('before');
    if (![workspaceId, taskId].every(isProposalIdentifier) || [...params.keys()].some(key => key !== 'before')
        || params.getAll('before').length > 1 || (before !== null && (!/^[1-9][0-9]{0,9}$/.test(before) || Number(before) > 2147483647 || String(Number(before)) !== before))) return failure(422);
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals${before ? `?before=${before}` : ''}`, {
            headers, signal, cache: 'no-store', redirect: 'error',
        });
        if (response.status !== 200) {
            await response.body?.cancel();
            return failure([403, 404, 422].includes(response.status) ? response.status : 502);
        }
        const result = readTaskChanges(await response.json(), workspaceId, taskId);
        signal.throwIfAborted();
        return result ? Response.json(result, { headers: { 'Cache-Control': 'no-store' } }) : failure(502);
    } catch { return failure(502); }
}
