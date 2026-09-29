import { stagedBindingProxy } from "../../../../../../_shared/staged-proxy.ts";
export const runtime = "nodejs";
export async function GET(request: Request, context: { params: Promise<{ workspaceId: string; taskId: string }> }) {
    const { workspaceId, taskId } = await context.params;
    return stagedBindingProxy(request, workspaceId, taskId);
}
