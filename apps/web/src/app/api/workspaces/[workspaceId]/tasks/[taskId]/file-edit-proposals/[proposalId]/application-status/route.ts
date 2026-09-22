import {
    proposalApplicationStatusProxy,
} from "../../../../../../../_shared/proposal-application-status-proxy.ts";

export const runtime = "nodejs";

type ProposalApplicationStatusContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
        proposalId: string;
    }>;
};

export async function GET(
    request: Request,
    context: ProposalApplicationStatusContext,
): Promise<Response> {
    const {
        workspaceId,
        taskId,
        proposalId,
    } = await context.params;

    // 路由只提取路径参数，访问边界和响应校验集中在代理中。
    return proposalApplicationStatusProxy(
        request,
        workspaceId,
        taskId,
        proposalId,
    );
}
