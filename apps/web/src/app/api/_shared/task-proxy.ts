import { isLocalMode, localHeaders } from './runtime.ts';
import { readTasks, readMessages, record } from '../../../features/workbench/task-data.ts';

export async function taskProxy(request: Request, workspaceId: string, taskId?: string, action?: 'messages' | 'title') {
    const fail = (status: number) => Response.json({ message: '任务服务暂时不可用' }, { status, headers: { 'Cache-Control': 'no-store' } });
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return fail(403);
        headers = localHeaders(request);
    } catch { return fail(403); }
    if (!/^[a-f0-9]{32}$/.test(workspaceId) || taskId !== undefined && !/^[a-f0-9]{32}$/.test(taskId)) return fail(422);
    const origin = request.headers.get('origin');
    if (action === 'title') {
        if (!origin) return fail(403);
        if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') return fail(415);
        try { const body: unknown = await request.json(); if (!record(body) || Object.keys(body).length) return fail(422); } catch { return fail(400); }
        headers = { ...headers, Origin: origin, 'Content-Type': 'application/json' };
    }
    const before = new URL(request.url).searchParams.get('before');
    if (before !== null && !/^[1-9][0-9]{0,9}$/.test(before)) return fail(422);
    const suffix = action ? `/${taskId}/${action}` : `?limit=20${before ? `&before=${before}` : ''}`;
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]);
    try {
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks${suffix}`, {
            method: action === 'title' ? 'POST' : 'GET', headers, body: action === 'title' ? '{}' : undefined,
            signal, cache: 'no-store', redirect: 'error',
        });
        if (response.status !== 200) return fail([403, 404, 422].includes(response.status) ? response.status : 502);
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        const value = action === 'messages' ? (() => { const messages = readMessages(raw); return messages && { messages }; })()
            : action === 'title' ? record(raw) && typeof raw.title === 'string' && raw.title.trim() && Array.from(raw.title).length <= 200 ? { title: raw.title } : null
            : readTasks(raw, workspaceId);
        if (!value) return fail(502);
        return Response.json(value, { headers: { 'Cache-Control': 'no-store' } });
    } catch { return fail(502); }
}
