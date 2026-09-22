import {
    fileEditProposalDecisionProxy,
} from "../../../../../../../_shared/file-edit-proposal-decision-proxy.ts";

export const runtime = "nodejs";

type FileEditProposalDecisionContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
        proposalId: string;
    }>;
};

export async function POST(
    request: Request,
    context: FileEditProposalDecisionContext,
): Promise<Response> {
    const {
        workspaceId,
        taskId,
        proposalId,
    } = await context.params;

    // 路由只取得路径参数；安全边界与上游处理集中在代理中。
    return fileEditProposalDecisionProxy(
        request,
        workspaceId,
        taskId,
        proposalId,
    );
}
