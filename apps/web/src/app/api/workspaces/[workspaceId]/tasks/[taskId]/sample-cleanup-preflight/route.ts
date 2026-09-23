import { taskSampleCleanupPreflightProxy } from "../../../../../_shared/task-sample-cleanup-preflight-proxy.ts";

export const runtime = "nodejs";

type TaskSampleCleanupPreflightContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
    }>;
};

export async function GET(
    request: Request,
    context: TaskSampleCleanupPreflightContext,
): Promise<Response> {
    const { workspaceId, taskId } = await context.params;

    // 路由仅提取资源标识；本机访问与响应契约由代理统一校验。
    return taskSampleCleanupPreflightProxy(request, workspaceId, taskId);
}
