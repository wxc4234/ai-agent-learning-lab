export type TaskItem = {
    external_id: string;
    workspace_id: string;
    conversation_id: string;
    title: string;
    created_at: string;
};
export type HistoryMessage = { role: 'user' | 'assistant'; content: string };
export function record(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}
export function readTask(value: unknown, workspaceId: string): TaskItem | null {
    if (!record(value) || value.workspace_id !== workspaceId ||
        typeof value.external_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.external_id) ||
        typeof value.conversation_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.conversation_id) ||
        typeof value.title !== 'string' || !value.title.trim() || Array.from(value.title).length > 200 ||
        typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at))) return null;
    return { external_id: value.external_id, workspace_id: workspaceId, conversation_id: value.conversation_id, title: value.title, created_at: value.created_at };
}
export function readTasks(value: unknown, workspaceId: string) {
    if (!record(value) || !Array.isArray(value.items) || value.items.length > 20 ||
        !(value.next_cursor === null || typeof value.next_cursor === 'string' && /^[1-9][0-9]{0,9}$/.test(value.next_cursor))) return null;
    const items = value.items.map(item => readTask(item, workspaceId));
    if (items.some(item => item === null) || new Set(items.map(item => item?.external_id)).size !== items.length) return null;
    return { items: items as TaskItem[], next_cursor: value.next_cursor as string | null };
}
export function readMessages(value: unknown): HistoryMessage[] | null {
    if (!record(value) || !Array.isArray(value.messages)) return null;
    const result: HistoryMessage[] = [];
    for (const message of value.messages) {
        if (!record(message) || !['user', 'assistant'].includes(String(message.role)) || typeof message.content !== 'string') return null;
        result.push({ role: message.role as HistoryMessage['role'], content: message.content });
    }
    return result;
}
