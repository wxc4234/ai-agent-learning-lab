import { codeQueryContextProxy } from "../../../../../_shared/code-query-context-proxy.ts";

export const runtime = "nodejs";

type CodeQueryContextRouteContext = {
    params: Promise<{ workspaceId: string; taskId: string }>;
};

export async function POST(request: Request, context: CodeQueryContextRouteContext): Promise<Response> {
    // Next路由只提取已匹配参数；来源、凭证、正文与公开投影由代理统一检查。
    const { workspaceId, taskId } = await context.params;
    return codeQueryContextProxy(request, workspaceId, taskId);
}
