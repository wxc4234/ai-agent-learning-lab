import { changeSetProxy } from "../../../../../../_shared/change-set-proxy.ts";
export async function POST(request: Request, { params }: { params: Promise<{ workspaceId: string; taskId: string; changeId: string }> }) {
    const { workspaceId, taskId, changeId } = await params;
    return changeSetProxy(request, workspaceId, taskId, changeId);
}
