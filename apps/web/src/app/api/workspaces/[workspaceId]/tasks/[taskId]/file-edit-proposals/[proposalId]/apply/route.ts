import {
    proposalExecutionProxy,
} from "../../../../../../../_shared/proposal-execution-proxy.ts";

export const runtime = "nodejs";

type ProposalExecutionContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
        proposalId: string;
    }>;
};

export async function POST(
    request: Request,
    context: ProposalExecutionContext,
): Promise<Response> {
    const {
        workspaceId,
        taskId,
        proposalId,
    } = await context.params;

    // 路由仅提取资源标识，安全边界和转发集中在代理中。
    return proposalExecutionProxy(
        request,
        workspaceId,
        taskId,
        proposalId,
    );
}
