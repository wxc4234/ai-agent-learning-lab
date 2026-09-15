import { taskProxy } from "../../../../../_shared/task-proxy.ts";
export const runtime = "nodejs";
export async function POST(request: Request, context: { params: Promise<{ workspaceId: string; taskId: string }> }) {
    const { workspaceId, taskId } = await context.params;
    return taskProxy(request, workspaceId, taskId, "title");
}
