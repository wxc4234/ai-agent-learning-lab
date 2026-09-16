import { isLocalMode, localHeaders } from './runtime.ts';
import {
    MAX_RUN_ID,
    MAX_RUN_PAGE_SIZE,
    isTaskRunIdentifier,
    parseRunInteger,
    readTaskRuns,
} from '../../../features/workbench/task-run-data.ts';

function failure(status: number): Response {
    return Response.json({ message: '任务运行历史暂时不可用' }, {
        status,
        headers: { 'Cache-Control': 'no-store' },
    });
}

export async function taskRunProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
): Promise<Response> {
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) return failure(403);
        // 验证本机访问边界，仅使用服务端配置的内部凭证。
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }
    if (!isTaskRunIdentifier(workspaceId) || !isTaskRunIdentifier(taskId)) {
        return failure(422);
    }
    const params = new URL(request.url).searchParams;
    // 拒绝未知和重复参数，避免各层对同一 URL 产生不同解释。
    if (
        [...params.keys()].some((key) => key !== 'before' && key !== 'limit') ||
        params.getAll('before').length > 1 || params.getAll('limit').length > 1
    ) {
        return failure(422);
    }
    const rawBefore = params.get('before');
    const rawLimit = params.get('limit');
    const before = rawBefore === null ? null : parseRunInteger(rawBefore, MAX_RUN_ID);
    const limit = rawLimit === null ? 20 : parseRunInteger(rawLimit, MAX_RUN_PAGE_SIZE);
    if ((rawBefore !== null && before === null) || limit === null) return failure(422);

    // 重建查询字符串，不转发客户端的原始参数或请求头。
    const query = new URLSearchParams({ limit: String(limit) });
    if (before !== null) query.set('before', String(before));
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]);
    try {
        signal.throwIfAborted();
        const response = await fetch(
            `${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks/${taskId}/runs?${query}`,
            { method: 'GET', headers, signal, cache: 'no-store', redirect: 'error' },
        );
        signal.throwIfAborted();
        if (response.status !== 200) {
            // 错误正文不进入浏览器；主动释放不再读取的上游响应体。
            await response.body?.cancel();
            return failure([403, 404, 422].includes(response.status) ? response.status : 502);
        }
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        const page = readTaskRuns(raw, workspaceId, taskId, before, limit);
        if (page === null) return failure(502);
        // 不复制上游 Cookie 等响应头；取消与超时覆盖正文读取阶段。
        return Response.json(page, { headers: { 'Cache-Control': 'no-store' } });
    } catch {
        // 沿用已有读取代理约定，网络、解析、超时和取消均安全返回 502。
        return failure(502);
    }
}
