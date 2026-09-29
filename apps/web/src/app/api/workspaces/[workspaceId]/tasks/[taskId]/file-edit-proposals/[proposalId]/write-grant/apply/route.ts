import { proposalExecutionProxy } from "../../../../../../../../_shared/proposal-execution-proxy.ts";

export async function POST(request: Request, context: { params: Promise<{ workspaceId: string; taskId: string; proposalId: string }> }) {
    const { workspaceId, taskId, proposalId } = await context.params;
    return proposalExecutionProxy(request, workspaceId, taskId, proposalId, true);
}
