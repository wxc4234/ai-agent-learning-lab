"use client";

import WorkbenchIcon from "./workbench-icon";
import TaskActions from "./task-actions";
import LoadingPlaceholder from "./loading-placeholder";
import CreateWorkspace from "@/features/workspaces/components/create-workspace";
import { DropdownMenu } from "radix-ui";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import type { WorkspaceListItem } from "@/features/workspaces/workspace-list";
import { useWorkbench } from "../workbench-session";
import { readTasks, type TaskItem } from "../task-data";
import WorkspaceDirectoryPanel from "./workspace-directory-panel";

function PenIcon() {
    return (
        <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
        >
            <path d="m14 5 5 5M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15l-1 5Z" />
            <path d="M13 20h7" />
        </svg>
    );
}
function FolderIcon() {
    return (
        <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            aria-hidden="true"
        >
            <path d="M3 7V5h6l2 2h10v12H3V7Z" />
        </svg>
    );
}
function ProjectGroup({ project }: { project: WorkspaceListItem }) {
    const {
        selection,
        select,
        busy,
        revision,
        refresh,
        deletion,
    } = useWorkbench();

    const [open, setOpen] = useState(
        selection?.workspace.external_id === project.external_id,
    );
    const [settings, setSettings] = useState(false);
    const [items, setItems] = useState<TaskItem[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    // 刷新回到第一页，但保留已有列表，避免用 React key 重建整个项目。
    const [pagination, setPagination] = useState<{ revision: number; before: string | null }>({ revision, before: null });
    const pageCursor = pagination.revision === revision ? pagination.before : null;
    const [hasLoaded, setHasLoaded] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retry, setRetry] = useState(0);
    const selected = selection?.workspace.external_id === project.external_id;
    useEffect(() => {
        // 选中新项目时展开；离开项目不销毁它的列表、分页和展开状态。
        let active = true;
        if (selected) queueMicrotask(() => { if (active) setOpen(true); });
        return () => { active = false; };
    }, [selected]);
    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]);
        queueMicrotask(() => {
            if (!controller.signal.aborted) setLoading(true);
        });
        void (async () => {
            try {
                const res = await fetch(
                    `/api/workspaces/${project.external_id}/tasks${pageCursor ? `?before=${pageCursor}` : ""}`,
                    {
                        cache: "no-store",
                        signal,
                    },
                );
                const data = readTasks(await res.json(), project.external_id);
                signal.throwIfAborted();
                if (!res.ok || !data) throw new Error();
                if (controller.signal.aborted) return;
                setItems((old) =>
                    pageCursor
                        ? [
                              ...old,
                              ...data.items.filter(
                                  (item) =>
                                      !old.some(
                                          (prior) =>
                                              prior.external_id ===
                                              item.external_id,
                                      ),
                              ),
                          ]
                        : data.items,
                );
                setHasLoaded(true);
                setCursor(data.next_cursor);
                setError(false);
            } catch {
                if (!controller.signal.aborted) setError(true);
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [open, project.external_id, pageCursor, revision, retry]);
    useEffect(() => {
        if (deletion?.phase !== "done" || deletion.workspace.external_id !== project.external_id) return;
        let active = true;
        queueMicrotask(() => {
            if (active) setItems(old => old.filter(task => task.external_id !== deletion.task.external_id));
        });
        return () => { active = false; };
    }, [deletion, project.external_id]);
    // 新创建任务立即可见；服务端列表随后刷新确认。
    const current = selection?.task;
    // 已确认不可访问的目标立即从旧列表排除，不等待后台刷新完成。
    const retained = items.filter(task => !(
        deletion?.phase === "done" &&
        deletion.workspace.external_id === project.external_id &&
        deletion.task.external_id === task.external_id
    ));
    const visible =
        current?.workspace_id === project.external_id &&
        !retained.some((t) => t.external_id === current.external_id)
            ? [current, ...retained]
            : retained;
    return (
        <li className="min-w-0">
            <div className="group flex items-center gap-0.5 rounded-lg hover:bg-black/5 dark:hover:bg-muted">
                <Button
                    variant="ghost"
                    className="h-11 min-w-0 flex-1 justify-start px-2 text-[18px] font-medium"
                    aria-expanded={open}
                    onClick={() => setOpen((v) => !v)}
                >
                    <WorkbenchIcon
                        name="chevron"
                        className={`!size-3 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
                    />
                    <FolderIcon />
                    <span className="truncate">{project.name}</span>
                </Button>
                <Button
                    variant="ghost"
                    size="icon-xs"
                    title="新建任务"
                    aria-label={`在 ${project.name} 新建任务`}
                    disabled={busy}
                    onClick={() => select(project)}
                >
                    <PenIcon />
                </Button>
                <DropdownMenu.Root>
                    <DropdownMenu.Trigger asChild>
                        <Button
                            variant="ghost"
                            size="icon-xs"
                            title="项目操作"
                            aria-label={`${project.name} 项目操作`}
                            className="text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 data-[state=open]:bg-black/5 data-[state=open]:opacity-100 dark:data-[state=open]:bg-muted [@media(hover:none)]:opacity-100"
                        >
                            <span aria-hidden="true">⋯</span>
                        </Button>
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Portal>
                        <DropdownMenu.Content
                            align="start"
                            sideOffset={4}
                            collisionPadding={12}
                            className="z-50 min-w-52 rounded-xl border border-border/80 bg-card/95 p-1.5 text-card-foreground shadow-lg backdrop-blur-xl outline-none"
                        >
                            <DropdownMenu.Item
                                disabled={busy}
                                onSelect={() => select(project)}
                                className="flex cursor-default items-center gap-3 rounded-md px-3 py-1.5 text-[18px] outline-none data-[highlighted]:bg-blue-500 data-[highlighted]:text-white data-[disabled]:pointer-events-none data-[disabled]:opacity-40 [&_svg]:size-4"
                            >
                                <PenIcon />
                                新建任务
                            </DropdownMenu.Item>
                            <DropdownMenu.Item
                                onSelect={() => setSettings(true)}
                                className="flex cursor-default items-center gap-3 rounded-md px-3 py-1.5 text-[18px] outline-none data-[highlighted]:bg-blue-500 data-[highlighted]:text-white"
                            >
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-4" aria-hidden="true">
                                    <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z" />
                                    <circle cx="12" cy="12" r="3" />
                                </svg>
                                项目设置
                            </DropdownMenu.Item>
                            <DropdownMenu.Separator className="mx-2 my-1 h-px bg-border" />
                            <DropdownMenu.Item
                                onSelect={refresh}
                                className="flex cursor-default items-center gap-3 rounded-md px-3 py-1.5 text-[18px] outline-none data-[highlighted]:bg-blue-500 data-[highlighted]:text-white"
                            >
                                <WorkbenchIcon name="refresh" className="size-4" />
                                刷新任务
                            </DropdownMenu.Item>
                        </DropdownMenu.Content>
                    </DropdownMenu.Portal>
                </DropdownMenu.Root>
            </div>
            {settings && (
                <section
                    aria-label={`${project.name} 设置`}
                    className="my-2 rounded-lg border border-border bg-background/60 p-3"
                >
                    <div className="flex items-center justify-between">
                        <span className="text-[18px] font-medium">
                            项目目录
                        </span>
                        <Button
                            size="icon-xs"
                            variant="ghost"
                            aria-label="关闭项目设置"
                            onClick={() => setSettings(false)}
                        >
                            ×
                        </Button>
                    </div>
                    <WorkspaceDirectoryPanel
                        workspaceId={project.external_id}
                    />
                </section>
            )}
            {open && (
                <ul
                    aria-label={`${project.name} 任务`}
                    className="mb-3 ml-5 space-y-0.5"
                >
                    {visible.map((task) => (
                        <li
                            key={task.external_id}
                            className={`group/task flex min-w-0 items-center rounded-lg hover:bg-black/5 dark:hover:bg-muted/60 ${selection?.task?.external_id === task.external_id ? "bg-black/10 dark:bg-muted" : ""}`}
                        >
                            <Button
                                variant="ghost"
                                disabled={busy}
                                aria-current={
                                    selection?.task?.external_id ===
                                    task.external_id
                                        ? "page"
                                        : undefined
                                }
                                title={task.title}
                                onClick={() => select(project, task)}
                                className="h-11 min-w-0 flex-1 justify-start px-2 text-[18px] font-normal hover:bg-transparent dark:hover:bg-transparent"
                            >
                                <span className="truncate">{task.title}</span>
                            </Button>
                            <TaskActions workspace={project} task={task} />
                        </li>
                    ))}
                    {loading && !hasLoaded && !visible.length && (
                        <li><LoadingPlaceholder label="正在读取任务列表" compact /></li>
                    )}
                    {hasLoaded && !loading && !error && !visible.length && (
                        <li className="px-2 py-1 text-[18px] text-muted-foreground">
                            点击笔形按钮开始任务
                        </li>
                    )}
                    {error && (
                        <li>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setRetry((n) => n + 1)}
                            >
                                读取失败，重试
                            </Button>
                        </li>
                    )}
                    {cursor && !loading && (
                        <li>
                            <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setPagination({ revision, before: cursor })}
                            >
                                展开更多
                            </Button>
                        </li>
                    )}
                </ul>
            )}
        </li>
    );
}
export default function WorkspaceSidebar() {
    const [createOpen, setCreateOpen] = useState(false);
    const {
        projects,
        localMode,
        loading,
        error,
        reload,
        selection,
        select,
        busy,
        deletion,
        checkDeletion,
    } = useWorkbench();
    return (
        <nav aria-label="项目和任务" className="mt-3 min-w-0">
            <Button
                variant="ghost"
                className="mb-5 h-11 w-full justify-start rounded-lg px-3 text-[18px] font-medium"
                disabled={busy || !selection}
                onClick={() => selection && select(selection.workspace)}
            >
                <PenIcon />
                新对话
            </Button>
            <div className="mb-2 flex items-center justify-between px-2">
                <h2 className="mr-auto text-[18px] text-muted-foreground">
                    项目
                </h2>
                <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="刷新项目"
                    title="刷新项目"
                    disabled={loading}
                    onClick={reload}
                >
                    <WorkbenchIcon name="refresh" />
                </Button>
                <CreateWorkspace localMode={localMode} modal={{ open: createOpen, onOpenChange: setCreateOpen, onCreated: reload }} />
            </div>
            {loading && (
                <p className="px-2 text-[18px] text-muted-foreground">
                    正在读取项目…
                </p>
            )}
            {error && (
                <Button variant="ghost" onClick={reload}>
                    项目读取失败，重试
                </Button>
            )}
            {!loading && !error && !projects.length && (
                <p className="px-2 text-[18px] text-muted-foreground">
                    添加项目后开始新对话。
                </p>
            )}
            <ul className="space-y-1">
                {projects.map((project) => (
                    <ProjectGroup
                        key={project.external_id}
                        project={project}
                    />
                ))}
            </ul>
            {deletion && (
                <section
                    aria-label="任务删除状态"
                    className="mx-2 mt-3 border-t border-border/60 pt-3 text-[18px]"
                >
                    <p className="truncate text-muted-foreground" title={deletion.task.title}>
                        {deletion.task.title}
                    </p>
                    <p
                        role="status"
                        aria-live="polite"
                        aria-atomic="true"
                        className="mt-1 text-muted-foreground"
                    >
                        {deletion.message}
                    </p>
                    {(
                        deletion.phase === "uncertain" ||
                        deletion.phase === "checking"
                    ) && (
                        <Button
                            variant="ghost"
                            disabled={deletion.phase === "checking"}
                            className="mt-1 h-9 px-0 text-[18px] underline underline-offset-4"
                            onClick={() => {
                                void checkDeletion();
                            }}
                        >
                            {deletion.phase === "checking"
                                ? "正在检查…"
                                : "检查任务状态"}
                        </Button>
                    )}
                </section>
            )}
        </nav>
    );
}
