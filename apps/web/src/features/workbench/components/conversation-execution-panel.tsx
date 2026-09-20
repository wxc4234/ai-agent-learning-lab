'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
    readConversationExecutionStatus,
    type ConversationExecutionStatus,
} from '../conversation-execution-data';

type QueryState =
    | { phase: 'idle' }
    | { phase: 'loading' }
    | { phase: 'error' }
    | {
        phase: 'ready';
        status: ConversationExecutionStatus;
        checkedAt: string;
    };

function formatTime(value: string): string {
    return new Date(value).toLocaleString('zh-CN', {
        hour12: false,
    });
}

export default function ConversationExecutionPanel({
    sessionId,
}: {
    sessionId: string;
}) {
    // 会话变化时重建内部状态，第一帧也不会显示上一个会话的结果。
    return <ExecutionQuery key={sessionId} sessionId={sessionId} />;
}

function ExecutionQuery({ sessionId }: { sessionId: string }) {
    const [state, setState] = useState<QueryState>({ phase: 'idle' });

    // 当前请求既用于取消网络，也用于判断返回结果是否仍然有效。
    const controllerRef = useRef<AbortController | null>(null);
    const [recovery, setRecovery] = useState<string | null>(null);
    const [recovering, setRecovering] = useState(false);

    useEffect(() => {
        return () => {
            controllerRef.current?.abort();
            controllerRef.current = null;
        };
    }, []);

    async function refresh(): Promise<void> {
        // ref 立即生效，阻止按钮状态更新前的重复点击。
        if (controllerRef.current !== null) {
            return;
        }

        const controller = new AbortController();
        controllerRef.current = controller;

        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(25_000),
        ]);

        // 刷新期间隐藏旧结果，避免把旧快照误读为当前状态。
        setState({ phase: 'loading' });

        try {
            const response = await fetch(
                `/api/sessions/${encodeURIComponent(sessionId)}/execution`,
                {
                    method: 'GET',
                    cache: 'no-store',
                    signal,
                },
            );

            if (response.status !== 200) {
                throw new Error('执行状态查询失败');
            }

            const raw: unknown = await response.json();

            signal.throwIfAborted();

            // 浏览器也校验公开契约，不能仅凭 HTTP 200 更新界面。
            const status = readConversationExecutionStatus(raw, sessionId);

            if (status === null) {
                throw new Error('执行状态响应不符合要求');
            }

            // 即使请求无法及时取消，卸载后的迟到结果也不能回写。
            if (
                controllerRef.current !== controller ||
                controller.signal.aborted
            ) {
                return;
            }

            setState({
                phase: 'ready',
                status,
                // 表示浏览器收到查询结果的时间，不是数据库事务时间。
                checkedAt: new Date().toISOString(),
            });
        } catch {
            // 主动卸载保持静默；超时、网络及契约错误明确显示失败。
            if (
                controllerRef.current === controller &&
                !controller.signal.aborted
            ) {
                setState({ phase: 'error' });
            }
        } finally {
            // 只清理本次请求，避免旧请求影响之后的请求。
            if (controllerRef.current === controller) {
                controllerRef.current = null;
            }
        }
    }

    async function recover(): Promise<void> {
        if (controllerRef.current) return;
        const controller = new AbortController();
        controllerRef.current = controller;
        setRecovering(true);
        setRecovery(null);
        setState({ phase: 'loading' });
        try {
            const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(25_000)]);
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/execution/recover`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', signal, cache: 'no-store',
            });
            signal.throwIfAborted();
            if (controllerRef.current !== controller) return;
            setRecovery(response.status === 204
                ? '恢复检查已完成，请刷新状态与运行历史；不会自动继续执行。'
                : response.status === 409
                    ? '无法确认原执行进程已退出，未解除占用。进程存活、身份未知或非本机执行时均会拒绝。'
                    : '恢复结果未确认，请重新查询状态后再决定是否重试。');
            // 隐藏恢复前的快照，不能把旧占用结果当成恢复后的事实。
            setState({ phase: 'idle' });
        } catch {
            if (controllerRef.current === controller && !controller.signal.aborted) {
                setRecovery('恢复结果未确认，请重新查询状态后再决定是否重试。');
                setState({ phase: 'idle' });
            }
        } finally {
            if (controllerRef.current === controller) {
                controllerRef.current = null;
                setRecovering(false);
            }
        }
    }

    const loading = state.phase === 'loading' || recovering;
    const buttonLabel =
        state.phase === 'idle'
            ? '查询状态'
            : state.phase === 'error'
                ? '重试查询'
                : loading
                    ? '查询中…'
                    : '刷新状态';

    return (
        <section
            aria-label="会话执行占用"
            className="mb-5 space-y-3 border-b border-border/60 pb-5"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-medium">会话执行占用</h3>
                <Button
                    type="button"
                    variant="outline"
                    disabled={loading}
                    onClick={() => void refresh()}
                >
                    {buttonLabel}
                </Button>
            </div>

            <div role="status" aria-live="polite" className="space-y-2 text-base">
                {state.phase === 'idle' && (
                    <p className="text-muted-foreground">
                        尚未查询，可手动查看会话占用。
                    </p>
                )}

                {loading && (
                    <p className="text-muted-foreground">{recovering ? '正在检查并恢复异常运行…' : '正在查询执行占用…'}</p>
                )}

                {state.phase === 'ready' && (
                    <>
                        <p className="font-medium">
                            {state.status.occupied
                                ? '查询时存在执行占用'
                                : '查询时未发现执行占用'}
                        </p>

                        {state.status.acquired_at !== null && (
                            <p className="text-muted-foreground">
                                占用获取时间：
                                <time dateTime={state.status.acquired_at}>
                                    {formatTime(state.status.acquired_at)}
                                </time>
                            </p>
                        )}

                        <p className="text-muted-foreground">
                            最近查询：
                            <time dateTime={state.checkedAt}>
                                {formatTime(state.checkedAt)}
                            </time>
                        </p>
                    </>
                )}
            </div>

            {state.phase === 'error' && (
                <p role="alert" className="text-base text-destructive">
                    查询失败，当前占用状态未知，请重试。
                </p>
            )}

            {state.phase === 'ready' && (
                <Button type="button" variant="outline" disabled={loading} onClick={() => void recover()}>
                    检查并恢复异常运行
                </Button>
            )}
            {recovery && <p role="status" className="text-base">{recovery}</p>}

            <p className="text-base text-muted-foreground">
                结果仅反映查询时刻。存在占用不代表进程仍在运行，
                占用时间也不能用于判断是否可以释放。
            </p>
        </section>
    );
}
