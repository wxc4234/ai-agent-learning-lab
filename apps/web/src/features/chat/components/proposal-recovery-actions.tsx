"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isProposalIdentifier } from "../../workbench/file-edit-proposal-data";

const labels: Record<string, string> = {
    created: "生成提案", approved: "批准", rejected: "拒绝", grant_issued: "发放许可", grant_revoked: "撤销许可",
    application_started: "领取应用", applied: "应用成功", not_applied: "未应用", uncertain: "结果未确认", restore_requested: "生成恢复提案",
};
type Event = { event: string; created_at: string; restore_proposal_id: string | null };
type Props = { workspaceId: string; taskId: string; proposalId: string };

export default function ProposalRecoveryActions(props: Props) {
    return <Actions key={`${props.workspaceId}:${props.taskId}:${props.proposalId}`} {...props} />;
}
function Actions({ workspaceId, taskId, proposalId }: Props) {
    const [events, setEvents] = useState<Event[] | null>(null);
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const [restoreId, setRestoreId] = useState<string | null>(null);
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => { const current = active.current; active.current = null; current?.abort(); }, []);
    async function run(restore: boolean) {
        if (active.current) return;
        const controller = new AbortController(); active.current = controller;
        setBusy(true); setMessage("");
        try {
            const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/${restore ? "restore-proposal" : "audit"}`, {
                method: restore ? "POST" : "GET", ...(restore ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "restore" }) } : {}),
                cache: "no-store", redirect: "error", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]),
            });
            if (!response.ok) { await response.body?.cancel(); throw new Error(); }
            const raw = await response.json();
            if (active.current !== controller) return;
            if (!raw || raw.workspace_id !== workspaceId || raw.task_id !== taskId || raw.proposal_id !== proposalId) throw new Error();
            if (restore) {
                if (typeof raw.restore_proposal_id !== "string" || !isProposalIdentifier(raw.restore_proposal_id)) throw new Error();
                setRestoreId(raw.restore_proposal_id);
                setMessage("恢复提案已生成。刷新改动列表，审阅并批准后再应用。");
            } else {
                if (!Array.isArray(raw.events) || raw.events.length > 100 || raw.events.some((item: Event) => !item || !Object.hasOwn(labels, item.event)
                    || typeof item.created_at !== "string" || !Number.isFinite(Date.parse(item.created_at))
                    || (item.restore_proposal_id !== null && (typeof item.restore_proposal_id !== "string" || !isProposalIdentifier(item.restore_proposal_id))))) throw new Error();
                setEvents(raw.events);
                const existing = raw.events.find((item: Event) => item.event === "restore_requested");
                if (existing) setRestoreId(existing.restore_proposal_id);
            }
        } catch {
            if (active.current === controller) setMessage(restore ? "恢复提案结果未确认或文件已变化，请查询审计记录。此操作不会直接改写文件。" : "审计记录读取失败。");
        } finally { if (active.current === controller) { active.current = null; setBusy(false); } }
    }
    return <details className="space-y-2">
        <summary className="cursor-pointer font-medium">应用审计与恢复</summary>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(false)}>查询审计记录</Button>
        {events && <ol className="space-y-1">{events.map((event, index) => <li key={index}>{labels[event.event]} · {new Date(event.created_at).toLocaleString()}</li>)}</ol>}
        {events?.some(event => event.event === "applied") && !restoreId && <>
            <p>生成恢复原文的反向提案，需要重新审批。文件已变化时会拒绝；不会自动覆盖或重试原应用。</p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(true)}>生成恢复提案</Button>
        </>}
        {restoreId && <p className="break-all">恢复提案：{restoreId}。可在刷新后的改动列表中审阅。</p>}
        {message && <p role="status">{message}</p>}
    </details>;
}
