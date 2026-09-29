import { isProposalIdentifier } from './file-edit-proposal-data.ts';
export type OwnedArea = { workspace_id: string; task_id: string; source_workspace_id: string; source_task_id: string; exported_change_id: string | null };
export function parseOwnedArea(raw: unknown): OwnedArea | null {
    if (!raw || typeof raw !== 'object') return null;
    const value = raw as Record<string, unknown>;
    if (!['workspace_id', 'task_id', 'source_workspace_id', 'source_task_id'].every(key => typeof value[key] === 'string' && isProposalIdentifier(value[key] as string))
        || (value.exported_change_id !== null && (typeof value.exported_change_id !== 'string' || !isProposalIdentifier(value.exported_change_id)))) return null;
    return { workspace_id: value.workspace_id as string, task_id: value.task_id as string,
        source_workspace_id: value.source_workspace_id as string, source_task_id: value.source_task_id as string,
        exported_change_id: value.exported_change_id as string | null };
}
