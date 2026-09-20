import { parseAgentStreamLine } from '../chat/agent-stream.ts';
import type { CompletedRunSummary } from '../chat/chat-state.ts';
import type { RunDetail } from './run-detail-data.ts';

export type HistoricalRunSummary = {
    status: 'done' | 'error';
    summary: CompletedRunSummary;
};

export function readHistoricalRunSummary(
    detail: RunDetail,
): HistoricalRunSummary | null {
    // 仅恢复已明确结束的成功或失败运行。
    // running、aborted 和未知状态不能借用事件冒充成功摘要。
    if (
        detail.finished_at === null ||
        (detail.status !== 'done' && detail.status !== 'error')
    ) {
        return null;
    }

    const terminals = detail.events.filter(
        event =>
            event.event_type === 'RUN_FINISHED' ||
            event.event_type === 'RUN_ERROR' ||
            event.event_type === 'RUN_ABORTED',
    );

    // 不从多个终态中猜选一个，也不把不同事件的字段拼在一起。
    if (terminals.length !== 1) {
        return null;
    }

    const terminal = terminals[0];
    const expectedType =
        detail.status === 'done' ? 'RUN_FINISHED' : 'RUN_ERROR';

    if (terminal.event_type !== expectedType) {
        return null;
    }

    try {
        // 复用实时流的严格校验：
        // 步数、Token、耗时和十进制金额沿用同一份数据契约。
        // type 最后写入，避免 payload 中的同名字段覆盖事件类型。
        const parsed = parseAgentStreamLine(
            JSON.stringify({
                ...terminal.payload,
                type: terminal.event_type,
            }),
        );

        if (parsed.type === 'RUN_FINISHED') {
            return {
                status: 'done',
                summary: {
                    stepsTaken: parsed.steps_taken,
                    metrics: parsed.metrics,
                },
            };
        }

        if (
            parsed.type === 'RUN_ERROR' &&
            parsed.steps_taken !== undefined &&
            parsed.metrics !== undefined
        ) {
            return {
                status: 'error',
                summary: {
                    stepsTaken: parsed.steps_taken,
                    metrics: parsed.metrics,
                },
            };
        }

        // 普通失败可能只保存 code/message，没有指标摘要。
        return null;
    } catch {
        // 历史数据不满足摘要契约时，不补零、不猜测金额。
        return null;
    }
}
