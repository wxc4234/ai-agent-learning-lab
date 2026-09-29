import { projectWriteAssessmentProxy } from "../../../../../../../../_shared/project-write-assessment-proxy.ts";

export const runtime = "nodejs";

type Context = {
    params: Promise<{
        workspaceId: string;
        taskId: string;
        proposalId: string;
    }>;
};

// 路由只解析资源参数；请求门禁、转发和响应投影集中在代理中。
export async function POST(
    request: Request,
    context: Context,
): Promise<Response> {
    const { workspaceId, taskId, proposalId } = await context.params;

    return projectWriteAssessmentProxy(
        request,
        workspaceId,
        taskId,
        proposalId,
    );
}
