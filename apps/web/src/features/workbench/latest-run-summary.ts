import { readTaskRuns } from './task-run-data.ts';
import { readRunDetail } from './run-detail-data.ts';
import { readHistoricalRunSummary } from './historical-run-summary.ts';

export type MetricsTask = { workspace_id: string; external_id: string; conversation_id: string };

// 先按任务归属查询最新 Run，再复用持久化终态事件；不回退到更早的成功记录。
export async function loadLatestRunSummary(task: MetricsTask, signal: AbortSignal) {
    const read = async (url: string) => {
        signal.throwIfAborted();
        const response = await fetch(url, { signal, cache: 'no-store', redirect: 'error' });
        if (!response.ok) throw new Error('运行指标读取失败');
        const value: unknown = await response.json();
        signal.throwIfAborted();
        return value;
    };
    const page = readTaskRuns(await read(`/api/workspaces/${task.workspace_id}/tasks/${task.external_id}/runs?limit=1`),
        task.workspace_id, task.external_id, null, 1);
    if (!page) throw new Error('运行列表不符合协议');
    const latest = page.items[0];
    if (!latest) return null;
    const detail = readRunDetail(await read(`/api/runs/${latest.run_id}`), String(latest.run_id));
    if (!detail) throw new Error('运行详情不符合协议');
    const starts = detail.events.filter(event => event.event_type === 'RUN_STARTED');
    if (starts.length !== 1 || starts[0].payload.session_id !== task.conversation_id) {
        throw new Error('运行会话不匹配');
    }
    return readHistoricalRunSummary(detail);
}
