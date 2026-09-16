import { record } from './task-data.ts';

export const MAX_RUN_ID = 2_147_483_647;
export const MAX_RUN_PAGE_SIZE = 50;

export type TaskRunItem = {
    run_id: number;
    status: string;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
};

export type TaskRunPage = {
    workspace_id: string;
    task_id: string;
    items: TaskRunItem[];
    next_cursor: string | null;
};

// 长度检查避免正则 $ 接受末尾换行。
export function isTaskRunIdentifier(value: string): boolean {
    return value.length === 32 && /^[a-f0-9]{32}$/.test(value);
}

// 请求参数与响应游标统一使用规范十进制正整数。
export function parseRunInteger(value: string, maximum: number): number | null {
    if (value.length > 10 || !/^[1-9][0-9]*$/.test(value) || value.trim() !== value) {
        return null;
    }
    const result = Number(value);
    return Number.isSafeInteger(result) && result <= maximum ? result : null;
}

function isTimestamp(value: unknown): value is string {
    return typeof value === 'string' && Number.isFinite(Date.parse(value));
}

export function readTaskRuns(
    value: unknown,
    workspaceId: string,
    taskId: string,
    before: number | null,
    limit: number,
): TaskRunPage | null {
    // 独立维护参数边界，并核对 URL 指定的项目与任务。
    if (
        !isTaskRunIdentifier(workspaceId) || !isTaskRunIdentifier(taskId) ||
        !Number.isInteger(limit) || limit < 1 || limit > MAX_RUN_PAGE_SIZE ||
        (before !== null && (!Number.isInteger(before) || before < 1 || before > MAX_RUN_ID)) ||
        !record(value) || value.workspace_id !== workspaceId || value.task_id !== taskId ||
        !Array.isArray(value.items) || value.items.length > limit
    ) {
        return null;
    }
    const items: TaskRunItem[] = [];
    // 严格递减同时检查排序、重复与请求游标边界。
    let upperBound = before ?? MAX_RUN_ID + 1;
    for (const item of value.items) {
        if (
            !record(item) || typeof item.run_id !== 'number' ||
            !Number.isInteger(item.run_id) || item.run_id < 1 ||
            item.run_id > MAX_RUN_ID || item.run_id >= upperBound ||
            typeof item.status !== 'string' || item.status.length === 0 ||
            !isTimestamp(item.started_at)
        ) {
            return null;
        }
        if (item.finished_at === null) {
            // 未结束的运行不伪造最终耗时。
            if (item.duration_ms !== null) return null;
        } else if (
            !isTimestamp(item.finished_at) ||
            Date.parse(item.finished_at) < Date.parse(item.started_at) ||
            typeof item.duration_ms !== 'number' ||
            !Number.isSafeInteger(item.duration_ms) || item.duration_ms < 0
        ) {
            return null;
        }
        // 重建公开字段；未知状态保留原值，不擅自归为终态。
        items.push({
            run_id: item.run_id,
            status: item.status,
            started_at: item.started_at,
            finished_at: item.finished_at,
            duration_ms: item.duration_ms,
        });
        upperBound = item.run_id;
    }
    const cursor = value.next_cursor;
    if (cursor !== null && (
        typeof cursor !== 'string' || parseRunInteger(cursor, MAX_RUN_ID) === null ||
        items.length !== limit || cursor !== String(items[items.length - 1]?.run_id)
    )) {
        return null;
    }
    return { workspace_id: workspaceId, task_id: taskId, items, next_cursor: cursor };
}
