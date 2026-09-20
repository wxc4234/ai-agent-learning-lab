import { isLocalMode, localHeaders } from './runtime.ts';

function result(status: number): Response {
    if (status === 204) return new Response(null, { status, headers: { 'Cache-Control': 'no-store' } });
    return Response.json({ message: status === 409
        ? '无法确认原执行进程已退出，未解除占用'
        : '恢复结果未确认，请重新查询执行状态' }, {
        status, headers: { 'Cache-Control': 'no-store' },
    });
}

export async function executionRecoveryProxy(request: Request, sessionId: string): Promise<Response> {
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return result(403);
        headers = localHeaders(request);
        if (!request.headers.get('origin')) return result(403);
    } catch { return result(403); }
    if (!/^[a-f0-9]{32}$/.test(sessionId) || new URL(request.url).search) return result(422);
    try {
        // 只接受空对象；浏览器不得指定执行者、用户或强制恢复开关。
        const body: unknown = await request.json();
        if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).length) return result(422);
    } catch { return result(422); }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/sessions/${sessionId}/execution/recover`, {
            method: 'POST', headers: { ...headers, Origin: request.headers.get('origin')!, 'Content-Type': 'application/json' },
            body: '{}', cache: 'no-store', redirect: 'error', signal,
        });
        signal.throwIfAborted();
        if (response.status === 204) return result(204);
        if (response.status === 409) {
            const raw: unknown = await response.json();
            signal.throwIfAborted();
            if (raw && typeof raw === 'object' && 'code' in raw && raw.code === 'execution_recovery_refused') return result(409);
        }
        // 错误正文及凭证不透传，写操作失败不自动重试。
        return result([403, 404, 422].includes(response.status) ? response.status : 502);
    } catch { return result(502); }
}
