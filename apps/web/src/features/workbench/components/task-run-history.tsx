'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import LoadingPlaceholder from './loading-placeholder';
import {
    readTaskRuns,
    type TaskRunItem,
} from '../task-run-data';

const PAGE_SIZE = 20;

type PageRequest = {
    before: string | null;
};

type PageResult = {
    request: PageRequest;
    items: TaskRunItem[];
    nextCursor: string | null;
    hasLoaded: boolean;
    error: string | null;
};

type TaskRunHistoryProps = {
    workspaceId: string;
    taskId: string;
    onSelect: (runId: number) => void;
};

function runStatusLabel(status: string): string {
    switch (status) {
        case 'running':
            return '运行中';
        case 'done':
            return '已完成';
        case 'error':
            return '失败';
        case 'aborted':
            return '已取消';
        default:
            return `未知状态：${status}`;
    }
}

function formatStartedAt(value: string): string {
    return new Date(value).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    });
}

export default function TaskRunHistory({
    workspaceId,
    taskId,
    onSelect,
}: TaskRunHistoryProps) {
    // 每次刷新或翻页创建新的请求对象，即使游标相同也能重新请求。
    const [pageRequest, setPageRequest] = useState<PageRequest>({
        before: null,
    });

    const [result, setResult] = useState<PageResult | null>(null);

    // 请求与结果分开保存，加载期间不清空已展示的记录。
    const loading = result?.request !== pageRequest;
    const items = result?.items ?? [];
    const hasLoaded = result?.hasLoaded ?? false;
    const error = loading ? null : result?.error ?? null;

    useEffect(() => {
        const controller = new AbortController();
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(25_000),
        ]);

        // abort 用于停止网络读取；active 用于禁止旧回调更新状态。
        // 即使底层未及时响应取消，旧结果也不能写回。
        let active = true;

        async function loadPage() {
            try {
                const query = new URLSearchParams({
                    limit: String(PAGE_SIZE),
                });

                if (pageRequest.before !== null) {
                    query.set('before', pageRequest.before);
                }

                const response = await fetch(
                    `/api/workspaces/${workspaceId}/tasks/${taskId}/runs?${query}`,
                    {
                        signal,
                        cache: 'no-store',
                    },
                );

                if (!response.ok) {
                    throw new Error('运行历史读取失败');
                }

                const raw: unknown = await response.json();

                signal.throwIfAborted();

                const page = readTaskRuns(
                    raw,
                    workspaceId,
                    taskId,
                    pageRequest.before === null
                        ? null
                        : Number(pageRequest.before),
                    PAGE_SIZE,
                );

                if (page === null) {
                    throw new Error('运行历史响应不符合要求');
                }

                if (!active) {
                    return;
                }

                setResult((previous) => ({
                    request: pageRequest,
                    // 第一页刷新替换列表；后续页才追加。
                    items: pageRequest.before === null
                        ? page.items
                        : [...(previous?.items ?? []), ...page.items],
                    nextCursor: page.next_cursor,
                    hasLoaded: true,
                    error: null,
                }));
            } catch {
                // 任务切换或组件卸载引发的取消不显示错误。
                // 超时仍展示失败，允许用户重试。
                if (!active) {
                    return;
                }

                setResult((previous) => ({
                    request: pageRequest,
                    items: previous?.items ?? [],
                    nextCursor: previous?.nextCursor ?? null,
                    hasLoaded: previous?.hasLoaded ?? false,
                    error: pageRequest.before === null
                        ? '运行历史读取失败，请重试。'
                        : '更多运行记录读取失败，请重试。',
                }));
            }
        }

        void loadPage();

        return () => {
            active = false;
            controller.abort();
        };
    }, [workspaceId, taskId, pageRequest]);

    return (
        <section
            aria-label="历史运行"
            className="border-t border-border/60 pt-4"
        >
            <div className="flex items-center justify-between gap-2">
                <h3 className="text-base font-medium">
                    历史运行
                </h3>

                <Button
                    type="button"
                    variant="ghost"
                    disabled={loading}
                    onClick={() => setPageRequest({ before: null })}
                >
                    刷新
                </Button>
            </div>

            {items.length > 0 && (
                <ul className="mt-2 divide-y divide-border/50">
                    {items.map((item) => (
                        <li
                            key={item.run_id}
                            className="space-y-2 py-4 text-base"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <Button
                                    type="button"
                                    variant="ghost"
                                    className="shrink-0 px-2 text-base font-medium"
                                    aria-label={`查看运行 ${item.run_id}`}
                                    onClick={() => onSelect(item.run_id)}
                                >
                                    #{item.run_id}
                                </Button>

                                <span className="min-w-0 break-words text-right text-muted-foreground">
                                    {runStatusLabel(item.status)}
                                </span>
                            </div>

                            <time
                                dateTime={item.started_at}
                                title={new Date(
                                    item.started_at,
                                ).toLocaleString('zh-CN')}
                                className="block text-muted-foreground"
                            >
                                {formatStartedAt(item.started_at)}
                            </time>

                            {item.duration_ms !== null && (
                                <p className="text-muted-foreground">
                                    耗时：{item.duration_ms} ms
                                </p>
                            )}
                        </li>
                    ))}
                </ul>
            )}

            {loading && (
                <LoadingPlaceholder
                    compact
                    label="正在读取运行历史"
                />
            )}

            {!loading && !error && hasLoaded && items.length === 0 && (
                <p className="py-4 text-base text-muted-foreground">
                    这个任务还没有运行记录。
                </p>
            )}

            {error && (
                <div className="space-y-2 py-3">
                    <p role="alert" className="text-base text-destructive">
                        {error}
                    </p>

                    <Button
                        type="button"
                        variant="outline"
                        onClick={() => setPageRequest({
                            before: pageRequest.before,
                        })}
                    >
                        重试
                    </Button>
                </div>
            )}

            {!loading && !error && result?.nextCursor && (
                <Button
                    type="button"
                    variant="ghost"
                    className="mt-2 w-full"
                    onClick={() => setPageRequest({
                        before: result.nextCursor,
                    })}
                >
                    加载更多
                </Button>
            )}
        </section>
    );
}
