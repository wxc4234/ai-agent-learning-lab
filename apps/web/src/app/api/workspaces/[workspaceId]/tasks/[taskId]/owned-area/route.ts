import { ownedAreaProxy } from '../../../../../_shared/owned-area-proxy.ts';
async function proxy(request: Request, { params }: { params: Promise<{ workspaceId: string; taskId: string }> }) {
    const { workspaceId, taskId } = await params;
    return ownedAreaProxy(request, workspaceId, taskId);
}
export const GET = proxy;
export const POST = proxy;
