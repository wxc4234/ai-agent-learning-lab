import type { WorkspaceListItem } from '../workspaces/workspace-list.ts';

export type TaskItem = {
  external_id: string;
  workspace_id: string;
  conversation_id: string;
  title: string;
  created_at: string;
};

export type TaskDetail = {
    workspace: WorkspaceListItem;
    task: TaskItem;
};

export type HistoryMessage = { role: "user" | "assistant"; content: string };
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function readTask(value: unknown, workspaceId: string): TaskItem | null {
  if (
    !record(value) ||
    value.workspace_id !== workspaceId ||
    typeof value.external_id !== "string" ||
    !/^[a-f0-9]{32}$/.test(value.external_id) ||
    typeof value.conversation_id !== "string" ||
    !/^[a-f0-9]{32}$/.test(value.conversation_id) ||
    typeof value.title !== "string" ||
    !value.title.trim() ||
    Array.from(value.title).length > 200 ||
    typeof value.created_at !== "string" ||
    !Number.isFinite(Date.parse(value.created_at))
  )
    return null;
  return {
    external_id: value.external_id,
    workspace_id: workspaceId,
    conversation_id: value.conversation_id,
    title: value.title,
    created_at: value.created_at,
  };
}

export function readTaskDetail(
    value: unknown,
    workspaceId: string,
    taskId: string,
): TaskDetail | null {
    // 参数和返回数据都必须符合公开标识规则。
    // 解析器独立维护这个边界，避免以后在其他调用处被错误复用。
    const identifier = /^[a-f0-9]{32}$/;

    if (
        !identifier.test(workspaceId) ||
        !identifier.test(taskId) ||
        !record(value) ||
        !record(value.workspace)
    ) {
        return null;
    }

    const workspace = value.workspace;

    // 项目资料必须属于 URL 指定的项目。
    // 字符长度按 Unicode 码点计算，与已有名称和标题规则保持一致。
    if (
        workspace.external_id !== workspaceId ||
        typeof workspace.name !== 'string' ||
        !workspace.name.trim() ||
        Array.from(workspace.name).length > 100 ||
        typeof workspace.created_at !== 'string' ||
        !Number.isFinite(Date.parse(workspace.created_at))
    ) {
        return null;
    }

    // 复用任务字段校验，同时核对 task.workspace_id。
    // 详情读取还必须匹配具体 taskId，不能接受同项目中的其他任务。
    const task = readTask(value.task, workspaceId);

    if (!task || task.external_id !== taskId) {
        return null;
    }

    // 只复制公开字段，避免上游新增内部字段后被直接传给浏览器。
    return {
        workspace: {
            external_id: workspaceId,
            name: workspace.name,
            created_at: workspace.created_at,
        },
        task,
    };
}

export function readTasks(value: unknown, workspaceId: string) {
  if (
    !record(value) ||
    !Array.isArray(value.items) ||
    value.items.length > 20 ||
    !(
      value.next_cursor === null ||
      (typeof value.next_cursor === "string" &&
        /^[1-9][0-9]{0,9}$/.test(value.next_cursor))
    )
  )
    return null;
  const items = value.items.map((item) => readTask(item, workspaceId));
  if (
    items.some((item) => item === null) ||
    new Set(items.map((item) => item?.external_id)).size !== items.length
  )
    return null;
  return {
    items: items as TaskItem[],
    next_cursor: value.next_cursor as string | null,
  };
}
export function readMessages(value: unknown): HistoryMessage[] | null {
  if (!record(value) || !Array.isArray(value.messages)) return null;
  const result: HistoryMessage[] = [];
  for (const message of value.messages) {
    if (
      !record(message) ||
      !["user", "assistant"].includes(String(message.role)) ||
      typeof message.content !== "string"
    )
      return null;
    result.push({
      role: message.role as HistoryMessage["role"],
      content: message.content,
    });
  }
  return result;
}
