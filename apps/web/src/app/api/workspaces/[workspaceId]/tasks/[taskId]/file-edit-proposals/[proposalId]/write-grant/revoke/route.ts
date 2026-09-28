import { projectWriteGrantProxy } from "../../../../../../../../_shared/project-write-grant-proxy.ts";

export const runtime = "nodejs";

type Context = { params: Promise<{ workspaceId: string; taskId: string; proposalId: string }> };

export async function POST(request: Request, context: Context): Promise<Response> {
    const { workspaceId, taskId, proposalId } = await context.params;
    return projectWriteGrantProxy(request, workspaceId, taskId, proposalId, "revoke");
}
