import {
    conversationExecutionProxy,
} from '../../../_shared/conversation-execution-proxy.ts';

export const runtime = 'nodejs';

export async function GET(
    request: Request,
    context: {
        params: Promise<{ sessionId: string }>;
    },
): Promise<Response> {
    const { sessionId } = await context.params;

    return conversationExecutionProxy(request, sessionId);
}
