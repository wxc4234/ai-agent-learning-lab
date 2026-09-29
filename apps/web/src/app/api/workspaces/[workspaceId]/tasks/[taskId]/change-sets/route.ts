import { changeSetProxy } from "../../../../../_shared/change-set-proxy.ts";
export async function GET(request: Request, { params }: { params: Promise<{ workspaceId: string; taskId: string }> }) {
    const { workspaceId, taskId } = await params;
    return changeSetProxy(request, workspaceId, taskId);
}
