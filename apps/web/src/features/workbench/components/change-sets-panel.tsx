'use client';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { changeSetStates, parseChangeSet, type ChangeSet } from '../change-set-data';
export default function ChangeSetsPanel({ workspaceId, taskId }: { workspaceId: string; taskId: string }) {
    const [items, setItems] = useState<ChangeSet[]>([]);
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => { const c = active.current; active.current = null; c?.abort(); }, []);
    async function request(item?: ChangeSet, action?: string) {
        if (active.current) return;
        if (action === 'apply' && item) {
            // 未确认请求跨刷新仍不可重放；服务端状态机继续提供跨页面保护。
            try {
                const key = `change-apply:${workspaceId}:${taskId}:${item.change_id}`;
                if (sessionStorage.getItem(key)) { setMessage('已有应用提交记录，请查询变更组状态。'); return; }
                sessionStorage.setItem(key, 'submitted');
            } catch { setMessage('无法保存提交记录，本次未发送。'); return; }
        }
        const controller = new AbortController(); active.current = controller; setBusy(true); setMessage('');
        try {
            const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/change-sets${item ? '/' + item.change_id : ''}`, {
                method: item ? 'POST' : 'GET', ...(item ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) } : {}),
                cache: 'no-store', redirect: 'error', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]),
            });
            if (!response.ok) { await response.body?.cancel(); throw new Error(); }
            const raw = await response.json();
            if (active.current !== controller) return;
            if (raw?.workspace_id !== workspaceId || raw?.task_id !== taskId) throw new Error();
            if (item) {
                const result = parseChangeSet(raw.item);
                if (!result || result.change_id !== item.change_id) throw new Error();
                setItems(previous => previous.map(old => old.change_id === item.change_id ? result : old));
                setMessage(changeSetStates[result.status]);
            } else {
                if (!Array.isArray(raw.items) || raw.items.length > 50) throw new Error();
                const parsed = raw.items.map(parseChangeSet);
                if (parsed.some((value: unknown) => !value)) throw new Error();
                setItems(parsed);
                if (!parsed.length) setMessage('当前没有变更组。');
            }
        } catch { if (active.current === controller) setMessage('结果未确认或条件已变化。请查询状态，勿重复应用。'); }
        finally { if (active.current === controller) { active.current = null; setBusy(false); } }
    }
    return <section className="space-y-3" aria-label="通用变更组">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void request()}>查询变更组</Button>
        <p className="text-xs text-muted-foreground">最近50组。统一审阅新增、修改、删除与重命名；批准后显式应用，失败按整组恢复。</p>
        {items.map(item => <details key={item.change_id} className="rounded-xl border p-3 space-y-2">
            <summary className="cursor-pointer text-sm">{item.paths.length}个文件 · {changeSetStates[item.status]}</summary>
            <p className="break-all text-xs">{item.paths.join('、')}</p>
            <pre tabIndex={0} className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{item.diff}</pre>
            <div className="flex flex-wrap gap-2">
                {item.status === 'pending' && <><Button size="sm" disabled={busy} onClick={() => void request(item, 'approve')}>批准整组</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => void request(item, 'reject')}>拒绝整组</Button></>}
                {item.status === 'approved' && <Button size="sm" disabled={busy} onClick={() => void request(item, 'apply')}>应用整组修改</Button>}
                {['applied', 'uncertain', 'running'].includes(item.status) && <Button size="sm" variant="outline" disabled={busy} onClick={() => void request(item, 'restore')}>检查并恢复整组原文</Button>}
            </div>
            <p className="text-xs text-muted-foreground">{item.audit.map(a => a.event).join(' → ')}</p>
        </details>)}
        {message && <p role="status" className="text-xs">{message}</p>}
    </section>;
}
