import { isLocalMode, localHeaders } from "./runtime.ts";
import {
    isProposalIdentifier,
} from "../../../features/workbench/file-edit-proposal-data.ts";
import {
    readTaskSampleStatus,
} from "../../../features/workbench/task-sample-status-data.ts";

function failure(status: number): Response {
    // 固定公开错误，不泄露上游异常、目录或内部凭证。
    // 错误对象不提供登记状态，调用方不能把失败当作missing。
    return Response.json(
        {
            code: "sample_status_read_failed",
            message: "读取样例登记状态失败，不能据此判断当前登记状态",
        },
        {
            status,
            headers: { "Cache-Control": "no-store" },
        },
    );
}

export async function taskSampleStatusProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
): Promise<Response> {
    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return failure(403);
        }

        // 复用本机访问保护，只从服务端配置生成内部凭证。
        // 不复制浏览器的Cookie、Authorization或内部凭证请求头。
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }

    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
    ) {
        return failure(422);
    }

    const contentLength = request.headers.get("content-length");

    // 查询仅接受路径资源标识；正文流和声明的正文同样拒绝。
    // 不读取正文，避免为不支持的输入等待数据或进行额外分配。
    if (
        new URL(request.url).searchParams.size !== 0
        || request.body !== null
        || (contentLength !== null && contentLength !== "0")
        || request.headers.has("transfer-encoding")
    ) {
        return failure(422);
    }

    // 同一个取消信号覆盖发起请求和读取上游响应正文。
    const signal = AbortSignal.any([
        request.signal,
        AbortSignal.timeout(20_000),
    ]);

    try {
        signal.throwIfAborted();

        const baseUrl =
            process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

        const response = await fetch(
            `${baseUrl}/workspaces/${workspaceId}/tasks/${taskId}/sample-status`,
            {
                method: "GET",
                headers,
                signal,
                cache: "no-store",
                redirect: "error",
            },
        );

        signal.throwIfAborted();

        if (response.status !== 200) {
            // 不解析或转发上游错误正文，释放不用的响应流。
            await response.body?.cancel();
            signal.throwIfAborted();

            // 沿用只读代理约定，其余状态统一映射为安全网关失败。
            return failure(
                [403, 404, 422].includes(response.status)
                    ? response.status
                    : 502,
            );
        }

        const raw: unknown = await response.json();

        // 已收到响应头不代表查询成功；正文读取也可能超时或取消。
        signal.throwIfAborted();

        const snapshot = readTaskSampleStatus(
            raw,
            workspaceId,
            taskId,
        );

        if (snapshot === null) {
            return failure(502);
        }

        // 只返回解析后的公开快照，不复制Set-Cookie等上游响应头。
        return Response.json(snapshot, {
            headers: { "Cache-Control": "no-store" },
        });
    } catch {
        // 网络、取消、超时和非法JSON均表示查询失败，不自动重试。
        return failure(502);
    }
}
