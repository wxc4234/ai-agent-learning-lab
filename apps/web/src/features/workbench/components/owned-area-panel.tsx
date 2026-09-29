'use client';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { parseOwnedArea, type OwnedArea } from '../owned-area-data';
export default function OwnedAreaPanel({ workspaceId, taskId }: { workspaceId: string; taskId: string }) {
    const [current, setCurrent] = useState<OwnedArea | null>(null);
    const [copies, setCopies] = useState<OwnedArea[]>([]);
    const [loaded, setLoaded] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => { const c = active.current; active.current = null; c?.abort(); }, []);
    async function run(action?: 'create' | 'export') {
        if (active.current) return;
        if (action === 'create') {
            try {
                const key = `area-create:${workspaceId}:${taskId}`;
                if (sessionStorage.getItem(key)) { setMessage('已有创建提交记录，请查询副本。'); return; }
                sessionStorage.setItem(key, 'submitted');
            } catch { setMessage('无法保存提交标记，本次未发送。'); return; }
        }
        const controller = new AbortController(); active.current = controller; setBusy(true); setMessage('');
        try {
            const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/owned-area`, {
                method: action ? 'POST' : 'GET', ...(action ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) } : {}),
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]), cache: 'no-store', redirect: 'error',
            });
            if (!response.ok) { await response.body?.cancel(); throw new Error(); }
            const raw = await response.json();
            if (active.current !== controller) return;
            if (raw?.workspace_id !== workspaceId || raw?.task_id !== taskId) throw new Error();
            if (action) {
                const area = parseOwnedArea(raw.area);
                if (!area || (action === 'create' ? area.source_workspace_id !== workspaceId || area.source_task_id !== taskId : area.workspace_id !== workspaceId || area.task_id !== taskId)) throw new Error();
                if (action === 'create') setCopies([area]); else setCurrent(area);
                setMessage(action === 'create' ? '隔离任务已创建。' : '导出提案已保存，需在原任务审阅并批准。');
            } else {
                const area = raw.current === null ? null : parseOwnedArea(raw.current);
                if ((raw.current !== null && !area) || !Array.isArray(raw.copies) || raw.copies.length > 20) throw new Error();
                const parsed = raw.copies.map(parseOwnedArea);
                if (parsed.some((value: unknown) => !value)) throw new Error();
                setCurrent(area); setCopies(parsed); setLoaded(true);
            }
        } catch { if (active.current === controller) setMessage('结果未确认或源项目已变化，请查询隔离工作区记录。'); }
        finally { if (active.current === controller) { active.current = null; setBusy(false); } }
    }
    return <details className="space-y-2" aria-label="隔离工作区">
        <summary className="cursor-pointer text-sm font-medium">隔离工作区</summary>
        <p className="text-xs text-muted-foreground">副本持久保留在本机。修改不会写回原项目；导出会生成待审批变更组。Git、依赖、环境密钥及恢复目录不复制。</p>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void run()}>查询隔离工作区</Button>
        {loaded && !current && !copies.length && <Button size="sm" disabled={busy} onClick={() => void run('create')}>创建隔离编码任务</Button>}
        {copies.map(area => <p key={area.task_id} className="text-sm"><a className="underline" href={`/?workspace=${area.workspace_id}&task=${area.task_id}`}>打开隔离任务</a></p>)}
        {current && <>
            {!current.exported_change_id && <Button size="sm" disabled={busy} onClick={() => void run('export')}>导出为原项目变更组</Button>}
            <p className="text-sm"><a className="underline" href={`/?workspace=${current.source_workspace_id}&task=${current.source_task_id}`}>返回原任务审阅</a></p>
            {current.exported_change_id && <p className="text-xs break-all">已导出：{current.exported_change_id}。此副本的导出记录已固定。</p>}
        </>}
        {message && <p role="status" className="text-xs">{message}</p>}
    </details>;
}
