import { deleteTaskProxy } from '../../../../_shared/task-delete-proxy.ts';
import { taskProxy } from '../../../../_shared/task-proxy.ts';

export const runtime = 'nodejs';

type TaskDetailContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
    }>;
};

export async function GET(
    request: Request,
    context: TaskDetailContext,
): Promise<Response> {
    const { workspaceId, taskId } = await context.params;

    // 路由只负责参数提取；凭证、响应校验和错误处理由共享代理负责。
    return taskProxy(request, workspaceId, taskId, 'detail');
}

export async function DELETE(
    request: Request,
    context: TaskDetailContext,
): Promise<Response> {
    const { workspaceId, taskId } = await context.params;
    return deleteTaskProxy(request, workspaceId, taskId);
}
