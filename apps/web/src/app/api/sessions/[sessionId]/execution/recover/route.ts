import { executionRecoveryProxy } from '../../../../_shared/execution-recovery-proxy.ts';

export const runtime = 'nodejs';

export async function POST(request: Request, context: { params: Promise<{ sessionId: string }> }): Promise<Response> {
    const { sessionId } = await context.params;
    return executionRecoveryProxy(request, sessionId);
}
