'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import LoadingPlaceholder from './loading-placeholder';
import TaskRunHistory from './task-run-history';
import { readRunDetail, type RunDetail, type RunTimelineEvent } from '../run-detail-data';
import RunSummaryCard from '@/features/chat/components/run-summary-card';
import { readHistoricalRunSummary } from '../historical-run-summary';

type DetailState = { phase: 'loading' } | { phase: 'error' } | { phase: 'ready'; detail: RunDetail };

function eventLabel(type: string): string {
    switch (type) {
        case 'RUN_STARTED': return '开始运行';
        case 'TOOL_CALL_START': return '开始调用工具';
        case 'TOOL_CALL_RESULT': return '工具执行成功';
        case 'TOOL_CALL_ERROR': return '工具执行失败';
        case 'TEXT_MESSAGE_START': return '开始输出回答';
        case 'TEXT_MESSAGE_CONTENT': return '回答内容';
        case 'TEXT_MESSAGE_END': return '回答输出结束';
        case 'RUN_CANCELLATION_REQUESTED': return '请求取消';
        case 'EXECUTION_RECOVERED': return '原执行进程已退出，已恢复会话占用';
        case 'RUN_ABORTED': return '运行已取消';
        case 'RUN_FINISHED': return '运行完成';
        case 'RUN_ERROR': return '运行失败';
        default: return `未知事件：${type}`;
    }
}

function EventContent({ event }: { event: RunTimelineEvent }) {
    const payload = event.payload;
    // 仅展示公开的已知字段，文本由 React 转义，不执行工具结果中的 HTML。
    const fields = [
        ['tool_name', '工具'], ['arguments', '参数'], ['result', '结果'],
        ['message', '说明'], ['details', '详情'], ['chunk', '内容'],
    ] as const;
    return (
        <div className="mt-2 space-y-2 text-base">
            {fields.map(([field, label]) => {
                const value = payload[field];
                return typeof value === 'string' && value.length > 0 ? (
                    <div key={field}>
                        <p className="text-muted-foreground">{label}</p>
                        <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{value}</p>
                    </div>
                ) : null;
            })}
            {typeof payload.reason === 'string' && (
                <p>{payload.reason === 'user' ? '用户取消' : payload.reason === 'timeout' ? '运行超时' : payload.reason === 'owner_process_exited' ? '已确认原执行进程退出；没有自动重跑工具或模型' : '原因未记录'}</p>
            )}
            {typeof payload.duration_ms === 'number' && <p>工具耗时：{payload.duration_ms} ms</p>}
            {typeof payload.steps_taken === 'number' && <p>执行步骤：{payload.steps_taken}</p>}
        </div>
    );
}

function HistoricalRunDetail({
    runId,
    onRetry,
}: {
    runId: number;
    onRetry: () => void;
}) {
    const [state, setState] = useState<DetailState>({ phase: 'loading' });

    useEffect(() => {
        const controller = new AbortController();
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(25_000),
        ]);
        let active = true;

        async function load() {
            try {
                const response = await fetch(`/api/runs/${runId}`, {
                    signal,
                    cache: 'no-store',
                });

                if (!response.ok) {
                    throw new Error('读取历史运行失败');
                }

                const raw: unknown = await response.json();

                signal.throwIfAborted();

                const detail = readRunDetail(raw, String(runId));

                if (detail === null) {
                    throw new Error('历史运行响应不符合要求');
                }

                if (active) {
                    setState({ phase: 'ready', detail });
                }
            } catch {
                if (active) {
                    setState({ phase: 'error' });
                }
            }
        }

        void load();

        // 保留现有隔离：切换、返回和重试后，旧请求不能迟到回写。
        return () => {
            active = false;
            controller.abort();
        };
    }, [runId]);

    if (state.phase === 'loading') {
        return (
            <LoadingPlaceholder
                compact
                label="正在读取历史运行详情"
            />
        );
    }

    if (state.phase === 'error') {
        return (
            <div className="space-y-3 py-4">
                <p role="alert" className="text-base text-destructive">
                    运行详情读取失败，记录可能已不可访问，请重试。
                </p>
                <Button type="button" variant="outline" onClick={onRetry}>
                    重试详情
                </Button>
            </div>
        );
    }

    const { detail } = state;
    const historicalSummary = readHistoricalRunSummary(detail);

    return (
        <div className="mt-3 space-y-4">
            <p className="text-base text-muted-foreground">
                历史记录，只读展示
            </p>

            {detail.duration_ms !== null && (
                <p className="text-base">
                    总耗时：{detail.duration_ms} ms
                </p>
            )}

            {historicalSummary !== null ? (
                <RunSummaryCard
                    summary={historicalSummary.summary}
                    status={historicalSummary.status}
                />
            ) : (
                <p className="text-base text-muted-foreground">
                    暂无可用的运行摘要，可查看下方已记录的事件。
                </p>
            )}

            {detail.events.length === 0 ? (
                <p className="text-base text-muted-foreground">
                    这次运行没有已记录的事件。
                </p>
            ) : (
                <ol
                    aria-label={`运行 ${runId} 的事件`}
                    className="space-y-5 border-l border-border pl-4"
                >
                    {detail.events.map(event => (
                        <li key={event.id} className="min-w-0">
                            <h4 className="break-words text-base font-medium">
                                {eventLabel(event.event_type)}
                            </h4>
                            <time
                                dateTime={event.created_at}
                                title={new Date(event.created_at).toLocaleString(
                                    'zh-CN',
                                )}
                                className="text-base text-muted-foreground"
                            >
                                {new Date(event.created_at).toLocaleTimeString(
                                    'zh-CN',
                                    { hour12: false },
                                )}
                            </time>
                            <EventContent event={event} />
                        </li>
                    ))}
                </ol>
            )}
        </div>
    );
}

export default function TaskRunPanel({ workspaceId, taskId }: { workspaceId: string; taskId: string }) {
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [attempt, setAttempt] = useState(0);
    return (
        <div>
            {/* 隐藏而不卸载列表，返回后保留已加载分页。外层 Task key 隔离选择。 */}
            <div hidden={selectedRunId !== null}>
                <TaskRunHistory workspaceId={workspaceId} taskId={taskId} onSelect={runId => {
                    setAttempt(0);
                    setSelectedRunId(runId);
                }} />
            </div>
            {selectedRunId !== null && (
                <section aria-label="历史运行详情" className="border-t border-border/60 pt-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-base font-medium">运行 #{selectedRunId}</h3>
                        <Button type="button" variant="ghost" onClick={() => setSelectedRunId(null)}>返回列表</Button>
                    </div>
                    <HistoricalRunDetail key={`${selectedRunId}:${attempt}`} runId={selectedRunId} onRetry={() => setAttempt(value => value + 1)} />
                </section>
            )}
        </div>
    );
}
