import {
    fileEditProposalProxy,
} from "../../../../../../_shared/file-edit-proposal-proxy.ts";

export const runtime = "nodejs";

type FileEditProposalContext = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
        proposalId: string;
    }>;
};

export async function GET(
    request: Request,
    context: FileEditProposalContext,
): Promise<Response> {
    const {
        workspaceId,
        taskId,
        proposalId,
    } = await context.params;

    // 路由只提取路径参数，访问边界和响应校验集中在代理中。
    return fileEditProposalProxy(
        request,
        workspaceId,
        taskId,
        proposalId,
    );
}
