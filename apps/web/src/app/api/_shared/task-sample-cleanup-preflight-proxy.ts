import { isLocalMode, localHeaders } from "./runtime.ts";
import { isProposalIdentifier } from "../../../features/workbench/file-edit-proposal-data.ts";
import { readTaskSampleCleanupPreflight } from "../../../features/workbench/task-sample-cleanup-preflight-data.ts";

function failure(status: number): Response {
    // 固定公开错误；上游故障不能被解释为目录缺失或身份匹配。
    return Response.json(
        {
            code: "sample_cleanup_preflight_read_failed",
            message: "读取样例清理诊断失败，不能据此判断目录状态",
        },
        {
            status,
            headers: { "Cache-Control": "no-store" },
        },
    );
}

export async function taskSampleCleanupPreflightProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
): Promise<Response> {
    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return failure(403);
        }

        // 只使用服务端本机凭证，不转发浏览器的 Cookie 或认证请求头。
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }

    if (!isProposalIdentifier(workspaceId) || !isProposalIdentifier(taskId)) {
        return failure(422);
    }

    const contentLength = request.headers.get("content-length");

    // 诊断目标只由路径确定；查询、正文流和正文声明都不能改变观察范围。
    if (
        request.method !== "GET"
        || new URL(request.url).searchParams.size !== 0
        || request.body !== null
        || (contentLength !== null && contentLength !== "0")
        || request.headers.has("transfer-encoding")
    ) {
        return failure(422);
    }

    // 同一个取消信号覆盖上游连接和读取 JSON 正文。
    const signal = AbortSignal.any([
        request.signal,
        AbortSignal.timeout(20_000),
    ]);

    try {
        signal.throwIfAborted();

        const baseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
        const response = await fetch(
            `${baseUrl}/workspaces/${workspaceId}/tasks/${taskId}/sample-cleanup-preflight`,
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
            // 上游错误正文可能含内部信息，直接取消且不反射给浏览器。
            await response.body?.cancel();
            signal.throwIfAborted();

            return failure(
                [403, 404, 422].includes(response.status)
                    ? response.status
                    : 502,
            );
        }

        const raw: unknown = await response.json();
        signal.throwIfAborted();

        const snapshot = readTaskSampleCleanupPreflight(
            raw,
            workspaceId,
            taskId,
        );

        if (snapshot === null) {
            return failure(502);
        }

        // 只发送重新构建的三字段快照和不缓存头，不复制上游响应头。
        return Response.json(snapshot, {
            headers: { "Cache-Control": "no-store" },
        });
    } catch {
        // 网络、取消、超时、非法 JSON 均为读取失败；不自动重试。
        return failure(502);
    }
}
