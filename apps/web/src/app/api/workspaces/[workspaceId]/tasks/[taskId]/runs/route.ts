import { taskRunProxy } from '../../../../../_shared/task-run-proxy.ts';

export const runtime = 'nodejs';

type TaskRunContext = {
    params: Promise<{ workspaceId: string; taskId: string }>;
};

export async function GET(request: Request, context: TaskRunContext): Promise<Response> {
    const { workspaceId, taskId } = await context.params;
    // 路由只提取公开标识，访问边界和响应处理由代理维护。
    return taskRunProxy(request, workspaceId, taskId);
}
