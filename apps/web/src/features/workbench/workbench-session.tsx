"use client";

import {
    createContext,
    useContext,
    useEffect,
    useState,
    useRef,
    useCallback,
    type ReactNode,
} from "react";
import {
    readWorkspaceList,
    type WorkspaceListItem,
} from "@/features/workspaces/workspace-list";
import { readTaskDetail, record, type TaskItem } from "./task-data";
import { readTaskLocation, writeTaskLocation } from "./task-url";

type Selection = {
    key: string;
    workspace: WorkspaceListItem;
    task: TaskItem | null;
};

type TaskDeletion = {
    workspace: WorkspaceListItem;
    task: TaskItem;
    phase: "pending" | "uncertain" | "checking" | "done" | "rejected";
    message: string;
};

export type TaskCreationIntent = {
    draftKey: string;
    requestKey: string;
    title: string;
    prompt: string;
};

type Session = {
    localMode: boolean;
    leftOpen: boolean;
    rightOpen: boolean;
    setLeftOpen: (value: boolean) => void;
    setRightOpen: (value: boolean) => void;
    projects: WorkspaceListItem[];
    loading: boolean;
    error: string | null;
    selection: Selection | null;
    busy: boolean;
    revision: number;
    restoreStatus: "loading" | "ready" | "error";
    restoreError: string | null;
    retryRestore: () => void;
    setBusy: (value: boolean) => void;
    refresh: () => void;
    reload: () => void;
    select: (workspace: WorkspaceListItem, task?: TaskItem) => void;
    adopt: (task: TaskItem) => void;
    creationIntent: () => TaskCreationIntent | null;
    beginCreation: (prompt: string) => TaskCreationIntent | null;
    clearCreation: () => void;
    rename: (id: string, title: string) => void;
    deletion: TaskDeletion | null;
    deleteTask: (workspace: WorkspaceListItem, task: TaskItem) => Promise<void>;
    checkDeletion: () => Promise<void>;
};


const Context = createContext<Session | null>(null);
export function useWorkbench() {
    const value = useContext(Context);
    if (!value) throw new Error("WorkbenchProvider required");
    return value;
}
export function WorkbenchProvider({
    children,
    localMode = true,
}: {
    children: ReactNode;
    localMode?: boolean;
}) {
    const [leftOpen, setLeftOpen] = useState(true);
    const [rightOpen, setRightOpen] = useState(false);
    const [projects, setProjects] = useState<WorkspaceListItem[]>([]);
    const [selection, setSelection] = useState<Selection | null>(null);
    const [busy, setBusyState] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const [reloadVersion, setReloadVersion] = useState(0);
    const [restoreStatus, setRestoreStatus] = useState<
        'loading' | 'ready' | 'error'
    >(localMode ? 'loading' : 'ready');
    const [restoreError, setRestoreError] = useState<string | null>(null);

    const [deletion, setDeletion] = useState<TaskDeletion | null>(null);

    // ref 同步记录操作状态，避免 React 尚未重新渲染时重复提交。
    const deletionRef = useRef<TaskDeletion | null>(null);
    const deletionLockRef = useRef(false);

    // 异步回调读取最新选择，避免依赖某次 render 的旧闭包。
    const selectionRef = useRef<Selection | null>(null);
    const projectsRef = useRef<WorkspaceListItem[]>([]);
    const busyRef = useRef(false);
    // 未确认的创建属于项目草稿，不随 TaskChat 卸载丢失；不持久化未发送内容。
    const creationsRef = useRef(new Map<string, TaskCreationIntent>());
    const restoreControllerRef = useRef<AbortController | null>(null);

    // 只有没有任务定位参数时，项目列表才能自动打开默认草稿。
    const allowDefaultRef = useRef(false);

    const commitSelection = useCallback((next: Selection | null) => {
        selectionRef.current = next;
        setSelection(next);
    }, []);

    const setBusy = useCallback((value: boolean) => {
        busyRef.current = value;
        setBusyState(value);
    }, []);

    const refresh = useCallback(() => {
        setRevision(value => value + 1);
    }, []);

    const restoreFromUrl = useCallback(async () => {
        if (!localMode || busyRef.current) {
            return;
        }

        // 新恢复尝试先废弃旧请求；取消后的旧结果不能再设置 selection。
        restoreControllerRef.current?.abort();
        const controller = new AbortController();
        restoreControllerRef.current = controller;

        allowDefaultRef.current = false;
        setRestoreError(null);
        setRestoreStatus('loading');
        commitSelection(null);

        const location = readTaskLocation(new URL(window.location.href));

        if (location.kind === 'invalid') {
            setRestoreError('任务链接不完整或格式不正确，请从左侧重新选择任务。');
            setRestoreStatus('error');
            return;
        }

        if (location.kind === 'empty') {
            allowDefaultRef.current = true;

            // 项目列表可能已经读取完成，也可能仍在请求中。
            const first = projectsRef.current[0];

            if (first) {
                commitSelection({
                    key: crypto.randomUUID(),
                    workspace: first,
                    task: null,
                });
            }

            setRestoreStatus('ready');
            return;
        }

        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(10_000),
        ]);

        try {
            const response = await fetch(
                `/api/workspaces/${location.workspaceId}/tasks/${location.taskId}`,
                {
                    cache: 'no-store',
                    signal,
                },
            );

            if (!response.ok) {
                throw new Error();
            }

            const raw: unknown = await response.json();

            // 超时覆盖正文读取；用户改选后也不能接纳迟到响应。
            signal.throwIfAborted();

            const detail = readTaskDetail(
                raw,
                location.workspaceId,
                location.taskId,
            );

            if (!detail) {
                throw new Error();
            }

            commitSelection({
                key: detail.task.external_id,
                workspace: detail.workspace,
                task: detail.task,
            });
            setRestoreStatus('ready');
        } catch {
            // 用户主动改选或组件卸载时保持安静。
            // 超时来自另一个 signal，仍应显示可重试的恢复错误。
            if (controller.signal.aborted) {
                return;
            }

            setRestoreError('任务恢复失败，任务可能不可访问或本地服务暂时不可用。');
            setRestoreStatus('error');
        }
    }, [commitSelection, localMode]);

    useEffect(() => {
        // 挂载后读取浏览器地址；清理过的 StrictMode 挂载不能启动恢复。
        let active = true;
        queueMicrotask(() => {
            if (active) void restoreFromUrl();
        });

        return () => {
            active = false;
            restoreControllerRef.current?.abort();
        };
    }, [restoreFromUrl]);

    useEffect(() => {
        if (!localMode) {
            return;
        }

        const controller = new AbortController();
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(10_000),
        ]);

        void (async () => {
            try {
                const response = await fetch('/api/workspaces?limit=20', {
                    cache: 'no-store',
                    signal,
                });
                const raw: unknown = await response.json();

                signal.throwIfAborted();

                const data = readWorkspaceList(raw, 20);

                if (!response.ok || !data) {
                    throw new Error();
                }

                projectsRef.current = data.items;
                setProjects(data.items);
                setError(null);

                // 列表用于导航，不能覆盖 URL 指定的任务或用户的新选择。
                if (
                    allowDefaultRef.current &&
                    !selectionRef.current &&
                    data.items[0]
                ) {
                    commitSelection({
                        key: crypto.randomUUID(),
                        workspace: data.items[0],
                        task: null,
                    });
                }
            } catch {
                if (!controller.signal.aborted) {
                    setError('项目读取失败，请重试。');
                }
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false);
                }
            }
        })();

        return () => {
            controller.abort();
        };
    }, [commitSelection, localMode, reloadVersion]);

    const select = (workspace: WorkspaceListItem, task?: TaskItem) => {
        if (busyRef.current) {
            return;
        }

        if (task && task.workspace_id !== workspace.external_id) {
            return;
        }

        // 用户选择优先于正在恢复的 URL 请求。
        restoreControllerRef.current?.abort();
        allowDefaultRef.current = false;
        writeTaskLocation(task ?? null);
        setRestoreError(null);
        setRestoreStatus('ready');

        commitSelection({
            key: task?.external_id ?? creationsRef.current.get(workspace.external_id)?.draftKey ?? crypto.randomUUID(),
            workspace,
            task: task ?? null,
        });
    };

    const creationIntent = () => {
        const current = selectionRef.current;
        return current && !current.task
            ? creationsRef.current.get(current.workspace.external_id) ?? null
            : null;
    };

    const beginCreation = (prompt: string) => {
        const current = selectionRef.current;
        if (!current || current.task) return null;
        const existing = creationsRef.current.get(current.workspace.external_id);
        if (existing) return existing;
        const intent: TaskCreationIntent = {
            draftKey: current.key,
            requestKey: crypto.randomUUID().replaceAll("-", ""),
            title: Array.from(prompt).slice(0, 80).join(""),
            prompt,
        };
        creationsRef.current.set(current.workspace.external_id, intent);
        return intent;
    };

    const clearCreation = () => {
        const current = selectionRef.current;
        if (current) creationsRef.current.delete(current.workspace.external_id);
    };

    const adopt = (task: TaskItem) => {
        const current = selectionRef.current;

        if (!current || current.workspace.external_id !== task.workspace_id) {
            return;
        }

        // 创建成功后写入任务地址，但保留草稿 key，首轮请求继续运行。
        writeTaskLocation(task);
        commitSelection({
            ...current,
            task,
        });
        refresh();
    };

    const rename = (id: string, title: string) => {
        const current = selectionRef.current;

        if (current?.task?.external_id !== id) {
            return;
        }

        commitSelection({
            ...current,
            task: {
                ...current.task,
                title,
            },
        });
    };

    const updateDeletion = (next: TaskDeletion) => {
        deletionRef.current = next;
        setDeletion(next);
    };

    const finishDeletion = (target: TaskDeletion, message: string) => {
        const current = selectionRef.current;
        const location = readTaskLocation(new URL(window.location.href));

        const selectedTarget =
            current?.workspace.external_id === target.workspace.external_id &&
            current.task?.external_id === target.task.external_id;

        // 恢复详情期间 selection 可能为空，也要阻止旧恢复请求重新选中目标。
        const restoringTarget =
            !current &&
            location.kind === "task" &&
            location.workspaceId === target.workspace.external_id &&
            location.taskId === target.task.external_id;

        if (selectedTarget || restoringTarget) {
            restoreControllerRef.current?.abort();
            allowDefaultRef.current = false;
            writeTaskLocation(null);
            setRestoreError(null);
            setRestoreStatus("ready");

            commitSelection({
                key: crypto.randomUUID(),
                workspace: target.workspace,
                task: null,
            });
        }

        updateDeletion({
            ...target,
            phase: "done",
            message,
        });

        // 刷新列表并重置分页；不凭旧列表判断任务是否已经消失。
        refresh();
    };

    const deleteTask = async (
        workspace: WorkspaceListItem,
        task: TaskItem,
    ) => {
        const previous = deletionRef.current;

        if (
            busyRef.current ||
            deletionLockRef.current ||
            previous?.phase === "uncertain" ||
            task.workspace_id !== workspace.external_id
        ) {
            return;
        }

        deletionLockRef.current = true;

        const target: TaskDeletion = {
            workspace,
            task,
            phase: "pending",
            message: "正在删除任务…",
        };

        updateDeletion(target);

        // 浏览器等待时间略长于 BFF 的 20 秒预算，让 BFF 优先返回安全错误。
        const signal = AbortSignal.timeout(25_000);

        try {
            const response = await fetch(
                `/api/workspaces/${workspace.external_id}/tasks/${task.external_id}`,
                {
                    method: "DELETE",
                    cache: "no-store",
                    signal,
                },
            );

            signal.throwIfAborted();

            // 204 没有正文，不能再调用 response.json()。
            if (response.status === 204) {
                finishDeletion(target, "任务已删除。");
                return;
            }

            const raw: unknown = await response.json();
            signal.throwIfAborted();

            const code =
                record(raw) && typeof raw.code === "string"
                    ? raw.code
                    : null;

            if (response.status === 409 && code === "conversation_busy") {
                // 明确拒绝不会删除任务；保留当前选择，允许稍后手动重试。
                updateDeletion({
                    ...target,
                    phase: "rejected",
                    message: "该任务仍有执行占用，暂不能删除。请等待执行及收尾完成后重试。",
                });
                return;
            }

            if (response.status === 409 && code === "proposal_application_busy") {
                // 服务在DELETE之前拒绝，不能误报为删除结果未确认。
                updateDeletion({
                    ...target,
                    phase: "rejected",
                    message: "存在执行中或结果未确认的文件应用，暂不能删除任务。请先核对应用结果。",
                });
                return;
            }

            if (response.status === 409 && code === "task_run_unsettled") {
                updateDeletion({
                    ...target,
                    phase: "rejected",
                    message: "该任务存在未确认结束的运行，暂不能删除。请先检查执行状态。",
                });
                return;
            }

            if (
                response.status === 404 &&
                code === "workspace_not_accessible"
            ) {
                finishDeletion(target, "任务已不存在或不可访问，列表已刷新。");
                return;
            }

            const rejected =
                (
                    response.status === 403 &&
                    (
                        code === "local_mode_required" ||
                        code === "local_access_rejected" ||
                        code === "workspace_origin_rejected"
                    )
                ) ||
                (
                    response.status === 422 &&
                    code === "invalid_task_input"
                ) ||
                (
                    response.status === 400 &&
                    code === "invalid_task_request"
                ) ||
                (
                    response.status === 499 &&
                    code === "task_request_cancelled"
                );

            if (rejected) {
                updateDeletion({
                    ...target,
                    phase: "rejected",
                    message: "删除请求被拒绝或尚未提交，请检查本地服务后重试。",
                });
                return;
            }

            // 未知状态、畸形响应与网络失败都不能证明数据库没有提交。
            throw new Error();
        } catch {
            updateDeletion({
                ...target,
                phase: "uncertain",
                message: "删除结果未确认，请检查任务状态；不要重复提交删除。",
            });
        } finally {
            deletionLockRef.current = false;
        }
    };

    const checkDeletion = async () => {
        const target = deletionRef.current;

        if (
            deletionLockRef.current ||
            !target ||
            target.phase !== "uncertain"
        ) {
            return;
        }

        deletionLockRef.current = true;
        updateDeletion({
            ...target,
            phase: "checking",
            message: "正在检查任务状态…",
        });

        const signal = AbortSignal.timeout(10_000);

        try {
            // 查询准确的任务详情，避免分页列表缺失造成误判。
            const response = await fetch(
                `/api/workspaces/${target.workspace.external_id}/tasks/${target.task.external_id}`,
                {
                    cache: "no-store",
                    signal,
                },
            );

            signal.throwIfAborted();

            if (response.status === 404) {
                finishDeletion(
                    target,
                    "任务已不存在或不可访问，列表已刷新。",
                );
                return;
            }

            const raw: unknown = await response.json();
            signal.throwIfAborted();

            const detail = readTaskDetail(
                raw,
                target.workspace.external_id,
                target.task.external_id,
            );

            if (response.status !== 200 || !detail) {
                throw new Error();
            }

            // 读到任务仍存在，也不能证明先前删除请求已经停止执行。
            updateDeletion({
                ...target,
                phase: "uncertain",
                message: "任务目前仍存在，先前删除结果尚未确认，请稍后再次检查。",
            });
            refresh();
        } catch {
            updateDeletion({
                ...target,
                phase: "uncertain",
                message: "任务状态检查失败，请确认本地服务可用后再次检查。",
            });
        } finally {
            deletionLockRef.current = false;
        }
    };

    // URL 指定的项目可能不在项目列表第一页，仍需在导航中可见。
    const visibleProjects = selection
        ? [
            selection.workspace,
            ...projects.filter(
                project => project.external_id !== selection.workspace.external_id,
            ),
        ]
        : projects;

    return (
        <Context.Provider
            value={{
                localMode,
                leftOpen,
                rightOpen,
                setLeftOpen,
                setRightOpen,
                projects: visibleProjects,
                selection,
                loading,
                error,
                busy,
                revision,
                restoreStatus,
                restoreError,
                retryRestore: () => {
                    void restoreFromUrl();
                },
                setBusy,
                refresh,
                reload: () => {
                    setLoading(true);
                    setReloadVersion(value => value + 1);
                    refresh();
                },
                select,
                adopt,
                creationIntent,
                beginCreation,
                clearCreation,
                rename,
                deletion,
                deleteTask,
                checkDeletion,
            }}
        >
            {children}
        </Context.Provider>
    );
}
