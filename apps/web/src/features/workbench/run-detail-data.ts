import { parseAgentStreamLine } from '../chat/agent-stream.ts';
import { record } from './task-data.ts';
import { MAX_RUN_ID, parseRunInteger, type TaskRunItem } from './task-run-data.ts';

export type RunTimelineEvent = {
    id: number;
    event_type: string;
    created_at: string;
    payload: Record<string, unknown>;
};
export type RunDetail = TaskRunItem & { events: RunTimelineEvent[] };

const STREAM_TYPES = new Set([
    'TOOL_CALL_START', 'TOOL_CALL_RESULT', 'TOOL_CALL_ERROR',
    'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END',
    'RUN_FINISHED', 'RUN_ERROR',
]);

function readPayload(type: string, payload: Record<string, unknown>): Record<string, unknown> | null {
    if (type === 'EXECUTION_RECOVERED') {
        return payload.reason === 'owner_process_exited' ? { reason: 'owner_process_exited' } : null;
    }
    if (type === 'RUN_STARTED') {
        if (typeof payload.session_id !== 'string' || !payload.session_id ||
            typeof payload.prompt_length !== 'number' ||
            !Number.isSafeInteger(payload.prompt_length) || payload.prompt_length < 0) return null;
        return { session_id: payload.session_id, prompt_length: payload.prompt_length };
    }
    // 取消写入的 RUN_ERROR 仅含 reason，不是流协议中的 code/message 分支。
    if (type === 'RUN_CANCELLATION_REQUESTED' || type === 'RUN_ABORTED' ||
        (type === 'RUN_ERROR' && payload.reason !== undefined)) {
        if (!['user', 'timeout', 'unknown'].includes(String(payload.reason)) || typeof payload.reason !== 'string') return null;
        return { reason: payload.reason };
    }
    if (!STREAM_TYPES.has(type)) return {};
    try {
        // type 由事件信封决定，不允许 payload 自行覆盖；解析器重建已知字段。
        const parsed = parseAgentStreamLine(JSON.stringify({ ...payload, type }));
        const publicPayload: Record<string, unknown> = { ...parsed };
        delete publicPayload.type;
        return publicPayload;
    } catch {
        return null;
    }
}

export function readRunDetail(value: unknown, runId: string): RunDetail | null {
    const id = parseRunInteger(runId, MAX_RUN_ID);
    if (id === null || !record(value) || value.run_id !== id || !Array.isArray(value.events)) return null;
    if (typeof value.status !== 'string' || !value.status ||
        typeof value.started_at !== 'string' || !Number.isFinite(Date.parse(value.started_at))) return null;
    if (value.finished_at === null) {
        if (value.duration_ms !== null) return null;
    } else if (typeof value.finished_at !== 'string' || !Number.isFinite(Date.parse(value.finished_at)) ||
        Date.parse(value.finished_at) < Date.parse(value.started_at) ||
        typeof value.duration_ms !== 'number' || !Number.isSafeInteger(value.duration_ms) || value.duration_ms < 0) return null;
    // 本接口按 Run 授权，不声称验证了特定 Task 的归属。
    const events: RunTimelineEvent[] = [];
    let previousId = 0;
    for (const event of value.events) {
        if (!record(event) || typeof event.id !== 'number' || !Number.isSafeInteger(event.id) ||
            event.id <= previousId || event.id > MAX_RUN_ID ||
            typeof event.event_type !== 'string' || !event.event_type ||
            typeof event.created_at !== 'string' || !Number.isFinite(Date.parse(event.created_at)) ||
            !record(event.payload)) return null;
        const payload = readPayload(event.event_type, event.payload);
        if (payload === null) return null;
        events.push({ id: event.id, event_type: event.event_type, created_at: event.created_at, payload });
        previousId = event.id;
    }
    return {
        run_id: id, status: value.status, started_at: value.started_at,
        finished_at: value.finished_at, duration_ms: value.duration_ms, events,
    };
}
