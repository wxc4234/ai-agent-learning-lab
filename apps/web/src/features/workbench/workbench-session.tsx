"use client";

import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from 'react';
import { readWorkspaceList, type WorkspaceListItem } from '@/features/workspaces/workspace-list';
import type { TaskItem } from './task-data';

type Selection = { key: string; workspace: WorkspaceListItem; task: TaskItem | null };
type Session = {
    localMode: boolean;
    leftOpen: boolean; rightOpen: boolean;
    setLeftOpen: (value: boolean) => void; setRightOpen: (value: boolean) => void;
    projects: WorkspaceListItem[]; loading: boolean; error: string | null;
    selection: Selection | null; busy: boolean; revision: number;
    setBusy: (value: boolean) => void; refresh: () => void; reload: () => void;
    select: (workspace: WorkspaceListItem, task?: TaskItem) => void;
    adopt: (task: TaskItem) => void;
    rename: (id: string, title: string) => void;
};
const Context = createContext<Session | null>(null);
export function useWorkbench() {
    const value = useContext(Context);
    if (!value) throw new Error('WorkbenchProvider required');
    return value;
}
export function WorkbenchProvider({ children, localMode = true }: { children: ReactNode; localMode?: boolean }) {
    const [leftOpen, setLeftOpen] = useState(true);
    // 空白对话不占用详情栏，用户按需展开运行信息。
    const [rightOpen, setRightOpen] = useState(false);
    const [projects, setProjects] = useState<WorkspaceListItem[]>([]);
    const [selection, setSelection] = useState<Selection | null>(null);
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const [reloadVersion, setReloadVersion] = useState(0);
    const refresh = useCallback(() => setRevision(n => n + 1), []);
    useEffect(() => {
        if (!localMode) return;
        const controller = new AbortController();
        void (async () => {
            try {
                const res = await fetch('/api/workspaces?limit=20', { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
                const data = readWorkspaceList(await res.json(), 20);
                if (!res.ok || !data) throw new Error();
                if (controller.signal.aborted) return;
                setProjects(data.items);
                setError(null);
                setSelection(previous => previous ?? (data.items[0] ? { key: crypto.randomUUID(), workspace: data.items[0], task: null } : null));
            } catch { if (!controller.signal.aborted) setError('项目读取失败，请重试。'); }
            finally { if (!controller.signal.aborted) setLoading(false); }
        })();
        return () => controller.abort();
    }, [reloadVersion, localMode]);
    return <Context.Provider value={{ localMode, leftOpen, rightOpen, setLeftOpen, setRightOpen, projects, selection, loading, error, busy, revision, setBusy, refresh,
        reload: () => { setLoading(true); setReloadVersion(n => n + 1); refresh(); },
        select: (workspace, task) => {
            if (!busy) setSelection({ key: task?.external_id ?? crypto.randomUUID(), workspace, task: task ?? null });
        },
        // 采用新任务时保持草稿的组件 key，避免中断首条消息请求。
        rename: (id, title) => setSelection(old => old?.task?.external_id === id ? { ...old, task: { ...old.task, title } } : old),
        adopt: task => { setSelection(old => old && old.workspace.external_id === task.workspace_id ? { ...old, task } : old); refresh(); },
    }}>{children}</Context.Provider>;
}
