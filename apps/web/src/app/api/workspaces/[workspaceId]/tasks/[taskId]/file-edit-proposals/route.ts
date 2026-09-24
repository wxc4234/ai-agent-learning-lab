import { taskChangesProxy } from '../../../../../_shared/task-changes-proxy.ts';
export const runtime = 'nodejs';
export async function GET(request: Request, context: { params: Promise<{ workspaceId: string; taskId: string }> }) {
    const { workspaceId, taskId } = await context.params;
    return taskChangesProxy(request, workspaceId, taskId);
}
