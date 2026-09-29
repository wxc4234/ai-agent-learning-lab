import { proposalRecoveryProxy } from "../../../../../../../../_shared/proposal-recovery-proxy.ts";

export async function POST(request: Request, { params }: { params: Promise<{ workspaceId: string; taskId: string; proposalId: string }> }) {
    const { workspaceId, taskId, proposalId } = await params;
    return proposalRecoveryProxy(request, workspaceId, taskId, proposalId, true);
}
