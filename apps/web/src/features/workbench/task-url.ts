export type TaskLocation =
    | { kind: 'empty' }
    | { kind: 'invalid' }
    | {
        kind: 'task';
        workspaceId: string;
        taskId: string;
    };

export function readTaskLocation(url: URL): TaskLocation {
    const workspaces = url.searchParams.getAll('workspace');
    const tasks = url.searchParams.getAll('task');

    if (workspaces.length === 0 && tasks.length === 0) {
        return { kind: 'empty' };
    }

    // 缺少一侧或重复参数都拒绝，避免不同读取方式得到不同资源。
    if (workspaces.length !== 1 || tasks.length !== 1) {
        return { kind: 'invalid' };
    }

    const workspaceId = workspaces[0];
    const taskId = tasks[0];
    const identifier = /^[a-f0-9]{32}$/;

    if (!identifier.test(workspaceId) || !identifier.test(taskId)) {
        return { kind: 'invalid' };
    }

    return {
        kind: 'task',
        workspaceId,
        taskId,
    };
}

export function writeTaskLocation(
    task: { workspace_id: string; external_id: string } | null,
): void {
    const url = new URL(window.location.href);

    // 只修改本功能拥有的参数，保留其他查询参数和 hash。
    url.searchParams.delete('workspace');
    url.searchParams.delete('task');

    if (task) {
        url.searchParams.set('workspace', task.workspace_id);
        url.searchParams.set('task', task.external_id);
    }

    // 保留框架已有 history state；更新地址不会重新挂载聊天组件。
    window.history.replaceState(
        window.history.state,
        '',
        `${url.pathname}${url.search}${url.hash}`,
    );
}
