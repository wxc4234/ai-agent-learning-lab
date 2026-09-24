import { isProposalIdentifier } from './file-edit-proposal-data.ts';

export type TaskChange = {
    proposal_id: string;
    relative_path: string;
    status: 'pending' | 'approved' | 'rejected';
    application_status: 'idle' | 'running' | 'applied' | 'not_applied' | 'uncertain';
    diff_truncated: boolean;
};
export type TaskChanges = { workspace_id: string; task_id: string; items: TaskChange[]; next_cursor: string | null };

export function readTaskChanges(raw: unknown, workspaceId: string, taskId: string): TaskChanges | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const value = raw as Record<string, unknown>;
    if (value.workspace_id !== workspaceId || value.task_id !== taskId || !Array.isArray(value.items)
        || value.items.length > 50 || (value.next_cursor !== null && (typeof value.next_cursor !== 'string'
            || !/^[1-9][0-9]{0,9}$/.test(value.next_cursor) || Number(value.next_cursor) > 2147483647 || String(Number(value.next_cursor)) !== value.next_cursor))) return null;
    const items: TaskChange[] = [];
    for (const item of value.items) {
        if (!item || typeof item !== 'object' || typeof item.proposal_id !== 'string' || !isProposalIdentifier(item.proposal_id)
            || typeof item.relative_path !== 'string' || !item.relative_path || item.relative_path.length > 8192
            || !['pending', 'approved', 'rejected'].includes(item.status)
            || !['idle', 'running', 'applied', 'not_applied', 'uncertain'].includes(item.application_status)
            || typeof item.diff_truncated !== 'boolean'
            || (item.application_status !== 'idle' && item.status !== 'approved')
            || (item.status === 'approved' && item.diff_truncated)
            || items.some(existing => existing.proposal_id === item.proposal_id)) return null;
        items.push({ proposal_id: item.proposal_id, relative_path: item.relative_path, status: item.status,
            application_status: item.application_status, diff_truncated: item.diff_truncated });
    }
    return { workspace_id: workspaceId, task_id: taskId, items, next_cursor: value.next_cursor as string | null };
}

export function changeStatus(change: TaskChange): string {
    if (change.application_status === 'applied') return '已应用';
    if (change.application_status === 'running') return '应用中';
    if (change.application_status === 'uncertain') return '应用结果未确认';
    if (change.application_status === 'not_applied') return '未应用';
    return { pending: '待审批', approved: '已批准 · 未应用', rejected: '已拒绝' }[change.status];
}
