import { isLocalMode, localHeaders } from "./runtime.ts";
import {
    readTaskDetail,
    readTasks,
    readMessages,
    record,
} from '../../../features/workbench/task-data.ts';

export async function taskProxy(
    request: Request,
    workspaceId: string,
    taskId?: string,
    action?: 'messages' | 'title' | 'detail',
): Promise<Response> {
    const fail = (status: number) => Response.json(
        { message: '任务服务暂时不可用' },
        {
            status,
            headers: { 'Cache-Control': 'no-store' },
        },
    );

    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return fail(403);
        }

        // 校验本地访问边界，并使用服务端配置的内部凭证。
        // 不转发浏览器提供的内部凭证或任意请求头。
        headers = localHeaders(request);
    } catch {
        return fail(403);
    }

    const identifier = /^[a-f0-9]{32}$/;

    if (
        !identifier.test(workspaceId) ||
        (taskId !== undefined && !identifier.test(taskId)) ||
        (action !== undefined && taskId === undefined)
    ) {
        return fail(422);
    }

    const origin = request.headers.get('origin');

    if (action === 'title') {
        if (!origin) {
            return fail(403);
        }

        if (
            request.headers.get('content-type')?.split(';')[0].trim() !==
            'application/json'
        ) {
            return fail(415);
        }

        try {
            const body: unknown = await request.json();

            if (!record(body) || Object.keys(body).length) {
                return fail(422);
            }
        } catch {
            return fail(400);
        }

        headers = {
            ...headers,
            Origin: origin,
            'Content-Type': 'application/json',
        };
    }

    const before = new URL(request.url).searchParams.get('before');

    if (before !== null && !/^[1-9][0-9]{0,9}$/.test(before)) {
        return fail(422);
    }

    // 默认请求仍是分页列表；详情使用独立分支，避免误请求列表接口。
    let suffix: string;

    if (action === 'detail') {
        suffix = `/${taskId}`;
    } else if (action) {
        suffix = `/${taskId}/${action}`;
    } else {
        suffix = `?limit=20${before ? `&before=${before}` : ''}`;
    }

    // 同时响应浏览器取消和代理超时，覆盖请求及响应正文读取。
    const signal = AbortSignal.any([
        request.signal,
        AbortSignal.timeout(20_000),
    ]);

    try {
        signal.throwIfAborted();

        const response = await fetch(
            `${
                process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'
            }/workspaces/${workspaceId}/tasks${suffix}`,
            {
                method: action === 'title' ? 'POST' : 'GET',
                headers,
                body: action === 'title' ? '{}' : undefined,
                signal,
                cache: 'no-store',
                redirect: 'error',
            },
        );

        // 只保留既有允许的错误状态，不透传上游错误正文。
        if (response.status !== 200) {
            return fail(
                [403, 404, 422].includes(response.status)
                    ? response.status
                    : 502,
            );
        }

        const raw: unknown = await response.json();

        signal.throwIfAborted();

        // JSON 能解析不代表符合接口约定，必须经过对应解析器。
        let value: unknown = null;

        if (action === 'detail') {
            if (taskId === undefined) {
                return fail(422);
            }

            value = readTaskDetail(raw, workspaceId, taskId);
        } else if (action === 'messages') {
            const messages = readMessages(raw);
            value = messages && { messages };
        } else if (action === 'title') {
            if (
                record(raw) &&
                typeof raw.title === 'string' &&
                raw.title.trim() &&
                Array.from(raw.title).length <= 200
            ) {
                value = { title: raw.title };
            }
        } else {
            value = readTasks(raw, workspaceId);
        }

        if (!value) {
            return fail(502);
        }

        // 只返回已校验的数据，不复制上游 Cookie 或其他响应头。
        return Response.json(value, {
            headers: { 'Cache-Control': 'no-store' },
        });
    } catch {
        // 沿用既有代理约定：网络、正文解析、取消和超时统一脱敏。
        return fail(502);
    }
}
