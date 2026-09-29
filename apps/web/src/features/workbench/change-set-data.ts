import { isProposalIdentifier } from './file-edit-proposal-data.ts';
export type ChangeSet = { change_id: string; status: string; paths: string[]; diff: string; audit: { event: string; at: string }[] };
export const changeSetStates: Record<string, string> = { pending: '待审批', approved: '已批准，待应用', rejected: '已拒绝', running: '执行未结束', applied: '已应用', rolled_back: '已恢复原文', uncertain: '结果未确认，需恢复检查' };
export function parseChangeSet(value: unknown): ChangeSet | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const v = value as Record<string, unknown>;
    if (typeof v.change_id !== 'string' || !isProposalIdentifier(v.change_id) || typeof v.status !== 'string' || !Object.hasOwn(changeSetStates, v.status)
        || typeof v.diff !== 'string' || v.diff.length > 65536 || !Array.isArray(v.paths) || !v.paths.length || v.paths.length > 32
        || v.paths.some(p => typeof p !== 'string' || !p || p.length > 1024 || p.startsWith('/') || p.split('/').includes('..'))
        || !Array.isArray(v.audit) || v.audit.length > 200 || v.audit.some(a => !a || typeof a.event !== 'string' || a.event.length > 30 || typeof a.at !== 'string' || !Number.isFinite(Date.parse(a.at)))) return null;
    return { change_id: v.change_id, status: v.status, diff: v.diff, paths: [...v.paths], audit: v.audit.map(a => ({ event: a.event, at: a.at })) };
}
