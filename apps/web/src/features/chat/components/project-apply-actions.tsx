"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { readProposalExecutionReceipt } from "../../workbench/proposal-execution-data";

type Props = { workspaceId: string; taskId: string; proposalId: string; grantId: string; revision: number };

export default function ProjectApplyActions(props: Props) {
    return <Actions key={`${props.workspaceId}:${props.taskId}:${props.proposalId}`} {...props} />;
}

function Actions({ workspaceId, taskId, proposalId, grantId, revision }: Props) {
    const [message, setMessage] = useState("");
    const [blocked, setBlocked] = useState(false);
    const active = useRef<AbortController | null>(null);
    const key = `project-apply:${workspaceId}:${taskId}:${proposalId}`;
    useEffect(() => {
        return () => { const controller = active.current; active.current = null; controller?.abort(); };
    }, [key]);

    async function apply() {
        if (blocked || active.current) return;
        // 标记写在网络请求前；刷新/切换详情后仍禁止盲目重放。
        try {
            if (sessionStorage.getItem(key) !== null) { setBlocked(true); return; }
            sessionStorage.setItem(key, "submitted");
        } catch { setBlocked(true); return; }
        setBlocked(true);
        const controller = new AbortController();
        active.current = controller;
        setMessage("正在应用…");
        try {
            const response = await fetch(`/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/apply`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ grant_id: grantId, revision }),
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]),
                cache: "no-store", redirect: "error",
            });
            const raw: unknown = await response.json();
            if (active.current !== controller) return;
            const receipt = readProposalExecutionReceipt(raw, workspaceId, taskId, proposalId);
            if (!response.ok || !receipt) throw new Error("unknown");
            setMessage(receipt.application_status === "applied" ? "文件修改已应用。"
                : receipt.application_status === "not_applied" ? "本次未应用，请检查文件变化并生成新提案。"
                : "应用结果未确认，请查询应用状态，勿重复提交。");
        } catch {
            if (active.current === controller) setMessage("应用结果未确认，请查询应用状态，勿重复提交。");
        } finally { if (active.current === controller) active.current = null; }
    }
    return <div className="space-y-2">
        <Button size="sm" disabled={blocked} onClick={() => void apply()}>应用已批准的项目修改</Button>
        {message && <p role="status" className="text-xs">{message}</p>}
        {blocked && !message && <p className="text-xs">本提案已有提交记录，请查询应用状态。</p>}
    </div>;
}
