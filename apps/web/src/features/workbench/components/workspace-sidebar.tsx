"use client";

import WorkbenchIcon from "./workbench-icon";
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { WorkspaceListItem } from '@/features/workspaces/workspace-list';
import { useWorkbench } from '../workbench-session';
import { readTasks, type TaskItem } from '../task-data';
import WorkspaceDirectoryPanel from './workspace-directory-panel';

function PenIcon() {
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m14 5 5 5M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15l-1 5Z" /><path d="M13 20h7" /></svg>;
}
function FolderIcon() {
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 7V5h6l2 2h10v12H3V7Z" /></svg>;
}
function ProjectGroup({ project }: { project: WorkspaceListItem }) {
    const { selection, select, busy, revision } = useWorkbench();
    const [open, setOpen] = useState(selection?.workspace.external_id === project.external_id);
    const [settings, setSettings] = useState(false);
    const [items, setItems] = useState<TaskItem[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [pageCursor, setPageCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retry, setRetry] = useState(0);
    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        void (async () => {
            try {
                const res = await fetch(`/api/workspaces/${project.external_id}/tasks${pageCursor ? `?before=${pageCursor}` : ''}`, { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
                const data = readTasks(await res.json(), project.external_id);
                if (!res.ok || !data) throw new Error();
                if (controller.signal.aborted) return;
                setItems(old => pageCursor ? [...old, ...data.items.filter(item => !old.some(prior => prior.external_id === item.external_id))] : data.items);
                setCursor(data.next_cursor); setError(false);
            } catch { if (!controller.signal.aborted) setError(true); }
            finally { if (!controller.signal.aborted) setLoading(false); }
        })();
        return () => controller.abort();
    }, [open, project.external_id, pageCursor, revision, retry]);
    // 新创建任务立即可见；服务端列表随后刷新确认。
    const current = selection?.task;
    const visible = current?.workspace_id === project.external_id && !items.some(t => t.external_id === current.external_id) ? [current, ...items] : items;
    return <li className="min-w-0">
        <div className="group flex items-center gap-0.5 rounded-lg hover:bg-black/5 dark:hover:bg-muted">
            <Button variant="ghost" className="h-11 min-w-0 flex-1 justify-start px-2 text-[18px] font-medium" aria-expanded={open} onClick={() => setOpen(v => !v)}>
                <WorkbenchIcon name="chevron" className={`!size-3 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`} /><FolderIcon /><span className="truncate">{project.name}</span>
            </Button>
            <Button variant="ghost" size="icon-xs" title="新建任务" aria-label={`在 ${project.name} 新建任务`} disabled={busy} onClick={() => select(project)}><PenIcon /></Button>
            <Button variant="ghost" size="icon-xs" title="项目设置" aria-label={`${project.name} 项目设置`} aria-expanded={settings} onClick={() => setSettings(v => !v)}><span aria-hidden="true">⋯</span></Button>
        </div>
        {settings && <section aria-label={`${project.name} 设置`} className="my-2 rounded-lg border border-border bg-background/60 p-3"><div className="flex items-center justify-between"><span className="text-[18px] font-medium">项目目录</span><Button size="icon-xs" variant="ghost" aria-label="关闭项目设置" onClick={() => setSettings(false)}>×</Button></div><WorkspaceDirectoryPanel workspaceId={project.external_id} /></section>}
        {open && <ul aria-label={`${project.name} 任务`} className="mb-3 ml-5 space-y-0.5">
            {visible.map(task => <li key={task.external_id}><Button variant="ghost" disabled={busy} aria-current={selection?.task?.external_id === task.external_id ? 'page' : undefined} title={task.title} onClick={() => select(project, task)} className={`h-11 w-full min-w-0 justify-start px-2 text-[18px] font-normal ${selection?.task?.external_id === task.external_id ? 'bg-black/10 dark:bg-muted' : ''}`}><span className="truncate">{task.title}</span></Button></li>)}
            {loading && <li className="px-2 py-1 text-[18px] text-muted-foreground">正在读取…</li>}
            {!loading && !error && !visible.length && <li className="px-2 py-1 text-[18px] text-muted-foreground">点击笔形按钮开始任务</li>}
            {error && <li><Button variant="ghost" size="sm" onClick={() => setRetry(n => n + 1)}>读取失败，重试</Button></li>}
            {cursor && !loading && <li><Button size="sm" variant="ghost" onClick={() => setPageCursor(cursor)}>展开更多</Button></li>}
        </ul>}
    </li>;
}
export default function WorkspaceSidebar() {
    const { projects, loading, error, reload, selection, select, busy, revision } = useWorkbench();
    return <nav aria-label="项目和任务" className="mt-3 min-w-0">
        <Button variant="ghost" className="mb-5 h-11 w-full justify-start rounded-lg px-3 text-[18px] font-medium" disabled={busy || !selection} onClick={() => selection && select(selection.workspace)}><PenIcon />新对话</Button>
        <div className="mb-2 flex items-center justify-between px-2"><h2 className="mr-auto text-[18px] text-muted-foreground">项目</h2><Button variant="ghost" size="icon-xs" aria-label="刷新项目" title="刷新项目" disabled={loading} onClick={reload}><WorkbenchIcon name="refresh" /></Button><Button asChild variant="ghost" size="icon-xs" title="添加项目"><Link href="/workspaces" aria-label="添加项目">+</Link></Button></div>
        {loading && <p className="px-2 text-[18px] text-muted-foreground">正在读取项目…</p>}
        {error && <Button variant="ghost" onClick={reload}>项目读取失败，重试</Button>}
        {!loading && !error && !projects.length && <p className="px-2 text-[18px] text-muted-foreground">添加项目后开始新对话。</p>}
        <ul className="space-y-1">{projects.map(project => <ProjectGroup key={`${project.external_id}:${revision}`} project={project} />)}</ul>
    </nav>;
}
