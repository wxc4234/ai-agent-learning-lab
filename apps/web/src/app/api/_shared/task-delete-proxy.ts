import { isLocalMode, localHeaders } from './runtime.ts';
import { record } from '../../../features/workbench/task-data.ts';

const UNCERTAIN = '任务删除结果未确认，请刷新任务列表后确认';

// 同时匹配状态和错误码；上游文案及内部字段不进入浏览器。
const ERRORS: Record<number, Record<string, string>> = {
    403: {
        local_mode_required: '任务功能仅支持本地模式',
        local_access_rejected: '本地服务拒绝访问，请检查运行配置',
        workspace_origin_rejected: '请求来源不被允许',
    },
    404: { workspace_not_accessible: '工作空间不存在或不可访问' },
    409: {
        task_run_unsettled: '存在未确认结束的运行，暂不能删除',
        conversation_busy: '该任务仍有执行占用，请等待执行及收尾完成后重试',
    },
    422: { invalid_task_input: '任务请求参数不符合要求' },
    500: { task_deletion_uncertain: UNCERTAIN },
};

function failure(status: number, code: string, message: string): Response {
    return Response.json({ code, message }, {
        status,
        headers: { 'Cache-Control': 'no-store' },
    });
}

export async function deleteTaskProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
): Promise<Response> {
    let headers: Record<string, string>;
    try {
        if (!isLocalMode()) {
            return failure(403, 'local_mode_required', '任务功能仅支持本地模式');
        }
        headers = localHeaders(request);
    } catch {
        return failure(403, 'local_access_rejected', '本地服务请求被拒绝，请检查运行配置');
    }
    const origin = request.headers.get('origin');
    if (!origin) {
        return failure(403, 'workspace_origin_rejected', '请求来源不被允许');
    }
    if (![workspaceId, taskId].every((id) => /^[a-f0-9]{32}$/.test(id))) {
        return failure(422, 'invalid_task_input', '任务请求参数不符合要求');
    }

    const cancelled = () => failure(499, 'task_request_cancelled', '请求已取消，尚未提交删除');
    if (request.signal.aborted) return cancelled();
    try {
        // DELETE 不接受任何正文，也无需 Content-Type；空白和 {} 同样拒绝。
        if ((await request.arrayBuffer()).byteLength !== 0) {
            return failure(422, 'invalid_task_input', '删除任务请求不能包含正文');
        }
    } catch {
        return request.signal.aborted ? cancelled()
            : failure(400, 'invalid_task_request', '任务请求无法解析');
    }
    if (request.signal.aborted) return cancelled();

    const timeout = AbortSignal.timeout(20_000);
    const signal = AbortSignal.any([request.signal, timeout]);
    try {
        signal.throwIfAborted();
        const response = await fetch(
            `${process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'}/workspaces/${workspaceId}/tasks/${taskId}`,
            {
                method: 'DELETE',
                headers: { ...headers, Origin: origin },
                signal,
                cache: 'no-store',
                redirect: 'error',
            },
        );
        signal.throwIfAborted();
        // 204 没有 JSON 正文；不复制上游 Cookie 或其他响应头。
        if (response.status === 204) {
            return new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });
        }
        const payload: unknown = await response.json();
        signal.throwIfAborted();
        const allowed = ERRORS[response.status];
        if (record(payload) && typeof payload.code === 'string' && allowed && Object.hasOwn(allowed, payload.code)) {
            return failure(response.status, payload.code, allowed[payload.code]);
        }
    } catch {
        // 转发后的取消/超时不能撤销数据库提交，必须重新读取列表确认。
        return failure(request.signal.aborted ? 499 : timeout.aborted ? 504 : 502,
            'task_deletion_uncertain', UNCERTAIN);
    }
    return failure(502, 'task_deletion_uncertain', UNCERTAIN);
}
