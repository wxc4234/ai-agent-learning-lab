import { isLocalMode, localHeaders } from "./runtime.ts";
import {
    isProposalIdentifier,
} from "../../../features/workbench/file-edit-proposal-data.ts";

import { readProposalApplicationStatus } from "../../../features/workbench/proposal-application-status-data.ts";

function failure(status: number): Response {
    // 不把上游SQL、内部路径或异常正文传给浏览器。
    return Response.json(
        { message: "提案应用状态暂时不可用" },
        {
            status,
            headers: { "Cache-Control": "no-store" },
        },
    );
}

export async function proposalApplicationStatusProxy(
    request: Request,
    workspaceId: string,
    taskId: string,
    proposalId: string,
): Promise<Response> {
    let headers: Record<string, string>;

    try {
        if (!isLocalMode()) {
            return failure(403);
        }

        // 验证本地访问边界，只使用服务端配置的内部凭证。
        // 不透传浏览器提供的Cookie、身份或内部凭证请求头。
        headers = localHeaders(request);
    } catch {
        return failure(403);
    }

    if (
        !isProposalIdentifier(workspaceId)
        || !isProposalIdentifier(taskId)
        || !isProposalIdentifier(proposalId)
    ) {
        return failure(422);
    }

    // 当前状态查询接口不接受查询参数，避免传入另一套身份或资源定位。
    if (new URL(request.url).searchParams.size !== 0) {
        return failure(422);
    }

    const signal = AbortSignal.any([
        request.signal,
        AbortSignal.timeout(20_000),
    ]);

    try {
        signal.throwIfAborted();

        const baseUrl =
            process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

        const response = await fetch(
            `${baseUrl}/workspaces/${workspaceId}/tasks/${taskId}`
                + `/file-edit-proposals/${proposalId}/application-status`,
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
            // 不读取错误正文，释放不再使用的上游响应流。
            await response.body?.cancel();

            // 沿用现有读取代理约定，其余上游状态统一视为网关失败。
            return failure(
                [403, 404, 422].includes(response.status)
                    ? response.status
                    : 502,
            );
        }

        const raw: unknown = await response.json();

        // 取消与超时也覆盖正文读取阶段。
        signal.throwIfAborted();

        const application = readProposalApplicationStatus(
            raw,
            workspaceId,
            taskId,
            proposalId,
        );

        if (application === null) {
            return failure(502);
        }

        // 不复制上游Set-Cookie或其他响应头。
        return Response.json(application, {
            headers: { "Cache-Control": "no-store" },
        });
    } catch {
        // 网络、解析、取消和超时统一安全失败，不自动重试。
        return failure(502);
    }
}
