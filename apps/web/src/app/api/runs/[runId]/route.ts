import { runDetailProxy } from '../../_shared/run-detail-proxy.ts';

export const runtime = 'nodejs';

export async function GET(
    request: Request,
    context: { params: Promise<{ runId: string }> },
): Promise<Response> {
    const { runId } = await context.params;
    return runDetailProxy(request, runId);
}
