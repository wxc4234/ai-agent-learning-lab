import {
    taskSampleStatusProxy,
} from "../../../../../_shared/task-sample-status-proxy.ts";

export const runtime = "nodejs";

type TaskSampleStatusContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
    }>;
};

export async function GET(
    request: Request,
    context: TaskSampleStatusContext,
): Promise<Response> {
    const {
        workspaceId,
        taskId,
    } = await context.params;

    // 路由只提取资源标识；访问保护与协议校验集中在代理中。
    return taskSampleStatusProxy(
        request,
        workspaceId,
        taskId,
    );
}
