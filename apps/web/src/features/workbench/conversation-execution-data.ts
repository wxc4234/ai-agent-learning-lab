import { record } from './task-data.ts';

export type ConversationExecutionStatus = {
    session_id: string;
    occupied: boolean;
    acquired_at: string | null;
};

export function readConversationExecutionStatus(
    value: unknown,
    sessionId: string,
): ConversationExecutionStatus | null {
    // 必须与本次请求的会话一致，避免把其他会话的状态展示到当前任务。
    if (
        !record(value) ||
        value.session_id !== sessionId ||
        typeof value.occupied !== 'boolean'
    ) {
        return null;
    }

    if (!value.occupied) {
        // 空闲状态必须明确返回 null，缺字段也属于契约错误。
        if (value.acquired_at !== null) {
            return null;
        }

        return {
            session_id: sessionId,
            occupied: false,
            acquired_at: null,
        };
    }

    // 占用状态必须带可解析的时间和明确时区。
    // 不根据时间新旧推断执行失效，也不在这里判断是否可以释放。
    if (
        typeof value.acquired_at !== 'string' ||
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(
            value.acquired_at,
        ) ||
        !Number.isFinite(Date.parse(value.acquired_at))
    ) {
        return null;
    }

    // 显式重建对象，上游即使增加 owner_token 等字段也不会被转发。
    return {
        session_id: sessionId,
        occupied: true,
        acquired_at: value.acquired_at,
    };
}
