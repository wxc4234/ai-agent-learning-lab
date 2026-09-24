'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import FileEditProposalDetailPanel from '@/features/chat/components/file-edit-proposal-detail';
import { changeStatus, readTaskChanges, type TaskChanges } from '../task-changes-data';

// 父组件按Task设置key；请求取消和active共同防止切任务后的迟到响应覆盖当前列表。
export default function TaskChangesPanel({ workspaceId, taskId, refreshKey }: {
    workspaceId: string; taskId: string; refreshKey: string;
}) {
    const [query, setQuery] = useState<{ before: string | null }>({ before: null });
    const [state, setState] = useState<{ query: object; refreshKey: string; data?: TaskChanges; failed?: boolean } | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        let active = true;
        async function load() {
            try {
                const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals${query.before ? `?before=${query.before}` : ''}`, {
                    cache: 'no-store', redirect: 'error', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]),
                });
                if (!response.ok) { await response.body?.cancel(); throw new Error('unavailable'); }
                const data = readTaskChanges(await response.json(), workspaceId, taskId);
                if (!data) throw new Error('invalid response');
                if (active) setState({ query, refreshKey, data });
            } catch {
                if (active) setState({ query, refreshKey, failed: true });
            }
        }
        void load();
        return () => { active = false; controller.abort(); };
    }, [workspaceId, taskId, query, refreshKey]);
    const loading = state?.query !== query || state?.refreshKey !== refreshKey;
    const data = !loading ? state?.data : undefined;
    return (
        <section aria-label="任务文件改动" className="space-y-4">
            <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium">修改提案</h3>
                <Button variant="ghost" size="sm" onClick={() => setQuery({ before: null })} disabled={loading}>刷新改动</Button>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">当前任务的提案与应用记录。批准不代表已写入文件；状态以最近查询为准。</p>
            {loading ? <p role="status" className="text-sm text-muted-foreground">正在读取改动…</p>
                : state?.failed ? <p role="alert" className="text-sm text-destructive">改动读取失败，请刷新重试。</p>
                : data?.items.length === 0 ? <p className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">当前没有修改提案</p>
                : data?.items.map(change => (
                    <details key={`${change.proposal_id}:${refreshKey}`} className="group rounded-xl border border-border bg-card">
                        <summary className="flex cursor-pointer list-none items-start gap-2 rounded-xl px-3 py-3 focus-visible:outline-2 focus-visible:outline-offset-2 [&::-webkit-details-marker]:hidden">
                            <span aria-hidden="true" className="mt-0.5 text-muted-foreground transition-transform group-open:rotate-90">›</span>
                            <span className="min-w-0 flex-1">
                            <span className="block break-all text-sm font-medium">{change.relative_path}</span>
                            <span className={`mt-1 block text-xs ${change.application_status === 'applied' ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>{changeStatus(change)}</span>
                            {change.diff_truncated && <span className="block text-xs text-destructive">Diff 不完整</span>}
                            </span>
                        </summary>
                        <div className="border-t border-border px-3 pb-3">
                            <FileEditProposalDetailPanel workspaceId={workspaceId} taskId={taskId} proposalId={change.proposal_id} />
                        </div>
                    </details>
                ))}
            {data?.next_cursor && <Button variant="outline" onClick={() => setQuery({ before: data.next_cursor })}>更早的改动</Button>}
            {query.before && <Button variant="ghost" onClick={() => setQuery({ before: null })}>返回最新改动</Button>}
        </section>
    );
}
