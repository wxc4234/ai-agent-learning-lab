import { isLocalMode, localHeaders } from './runtime.ts';
import { MAX_RUN_ID, parseRunInteger } from '../../../features/workbench/task-run-data.ts';
import { readRunDetail } from '../../../features/workbench/run-detail-data.ts';

function failure(status: number): Response {
    return Response.json({ message: '运行详情暂时不可用' }, {
        status, headers: { 'Cache-Control': 'no-store' },
    });
}

export async function runDetailProxy(request: Request, runId: string): Promise<Response> {
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return failure(403);
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }
    if (parseRunInteger(runId, MAX_RUN_ID) === null || new URL(request.url).search) return failure(422);
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(`${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/runs/${runId}`, {
            method: 'GET', headers, signal, cache: 'no-store', redirect: 'error',
        });
        signal.throwIfAborted();
        if (response.status !== 200) {
            // 不读取上游错误正文，也不复制 Cookie 等响应头。
            await response.body?.cancel();
            return failure([403, 404, 422].includes(response.status) ? response.status : 502);
        }
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        const detail = readRunDetail(raw, runId);
        if (detail === null) return failure(502);
        return Response.json(detail, { headers: { 'Cache-Control': 'no-store' } });
    } catch {
        // 与其他只读代理一致：取消、超时、网络或数据契约异常统一脱敏。
        return failure(502);
    }
}
