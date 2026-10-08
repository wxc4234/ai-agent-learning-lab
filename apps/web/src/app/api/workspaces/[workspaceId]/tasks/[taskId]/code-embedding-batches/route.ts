import { codeBatchSummariesProxy } from "../../../../../_shared/code-batch-summaries-proxy.ts";

export const runtime = "nodejs";

type CodeBatchSummariesRouteContext = {
    params: Promise<{ workspaceId: string; taskId: string }>;
};

export async function GET(request: Request, context: CodeBatchSummariesRouteContext): Promise<Response> {
    // Next路由只等待路径参数；本机门禁、有界读取与公开契约交给代理。
    const { workspaceId, taskId } = await context.params;
    return codeBatchSummariesProxy(request, workspaceId, taskId);
}
