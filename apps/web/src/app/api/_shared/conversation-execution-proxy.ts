import { isLocalMode, localHeaders } from './runtime.ts';
import {
    readConversationExecutionStatus,
} from '../../../features/workbench/conversation-execution-data.ts';

function failure(status: number): Response {
    return Response.json(
        { message: '会话执行状态暂时不可用' },
        {
            status,
            headers: { 'Cache-Control': 'no-store' },
        },
    );
}

async function discardBody(response: Response): Promise<void> {
    try {
        await response.body?.cancel();
    } catch {
        // 上游可能已经断开；丢弃失败不能导致读取或暴露错误正文。
    }
}

export async function conversationExecutionProxy(
    request: Request,
    sessionId: string,
): Promise<Response> {
    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return failure(403);
        }

        // 复用本地 Host、Origin、跨站来源和后端地址校验。
        // 内部凭证来自服务端配置，不转发浏览器提供的凭证或 Cookie。
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }

    // 本地任务会话使用服务端生成的 32 位小写十六进制标识。
    // 本接口没有查询参数，拒绝 user_id 等额外输入。
    if (
        !/^[a-f0-9]{32}$/.test(sessionId) ||
        new URL(request.url).search
    ) {
        return failure(422);
    }

    // 同时响应客户端取消和代理超时，覆盖请求及正文读取阶段。
    const signal = AbortSignal.any([
        request.signal,
        AbortSignal.timeout(20_000),
    ]);

    try {
        signal.throwIfAborted();

        const response = await fetch(
            `${
                process.env.API_BASE_URL ?? 'http://127.0.0.1:8000'
            }/sessions/${sessionId}/execution`,
            {
                method: 'GET',
                headers,
                signal,
                cache: 'no-store',
                redirect: 'error',
            },
        );

        signal.throwIfAborted();

        if (response.status !== 200) {
            // 只保留允许的错误状态，不读取上游错误正文。
            // 查询占用本身应返回 200，意外的 409 也视为上游异常。
            await discardBody(response);

            return failure(
                [403, 404, 422].includes(response.status)
                    ? response.status
                    : 502,
            );
        }

        const raw: unknown = await response.json();

        signal.throwIfAborted();

        const status = readConversationExecutionStatus(raw, sessionId);

        if (status === null) {
            return failure(502);
        }

        // 只输出校验后的公开字段，不复制上游 Cookie 或其他响应头。
        return Response.json(status, {
            headers: { 'Cache-Control': 'no-store' },
        });
    } catch {
        // 网络、超时、取消及响应解析失败统一脱敏。
        // 查询失败不能伪装成 occupied=false。
        return failure(502);
    }
}
