"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isProposalIdentifier } from "../../workbench/file-edit-proposal-data";
import { grantChangeObserved, readGrantReceipt, type GrantReceipt } from "../../workbench/project-write-grant-data";

import ProjectApplyActions from "./project-apply-actions";
import ProjectWriteAssessmentActions from "./project-write-assessment-actions";

type Props = { workspaceId: string; taskId: string; proposalId: string; approved: boolean };

export default function ProjectWriteGrantActions(props: Props) {
    // 即使父组件没有key，切换资源也会销毁旧请求实例。
    return <GrantActions key={`${props.workspaceId}:${props.taskId}:${props.proposalId}`} {...props} />;
}

function GrantActions({ workspaceId, taskId, proposalId, approved }: Props) {
    const [expanded, setExpanded] = useState(false);
    const [receipt, setReceipt] = useState<GrantReceipt | null>(null);
    const [busy, setBusy] = useState(false);
    const [blocked, setBlocked] = useState(true);
    const [message, setMessage] = useState("");
    const [confirm, setConfirm] = useState<"issue" | "revoke" | null>(null);
    const active = useRef<AbortController | null>(null);
    const guardKey = `project-write-grant:${workspaceId}:${taskId}:${proposalId}`;
    const available = [workspaceId, taskId, proposalId].every(isProposalIdentifier);
    const path = `/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant`;
    useEffect(() => () => { active.current?.abort(); active.current = null; }, []);

    async function run(operation: "read" | "issue" | "revoke") {
        if (!available || active.current) return;
        const previous = receipt?.grant;
        if (operation !== "read" && (blocked || !receipt || (operation === "issue" ? !approved || previous !== null : previous?.status !== "enabled"))) return;
        // 持久到当前标签页，关闭详情/切换任务/刷新都不能抹去未确认写请求。
        let marker: string | null;
        try {
            marker = sessionStorage.getItem(guardKey);
            if (operation !== "read") {
                if (marker !== null) { setBlocked(true); setMessage("有未确认的许可变更，请先查询状态。"); return; }
                marker = operation === "issue" ? "issue" : `revoke:${previous!.grant_id}`;
                sessionStorage.setItem(guardKey, marker);
            }
        } catch {
            setBlocked(true); setMessage("无法保存防重复记录，暂不能管理许可。"); return;
        }
        const controller = new AbortController();
        active.current = controller;
        setBusy(true); setConfirm(null); setBlocked(true); setMessage("");
        try {
            const response = await fetch(path + (operation === "revoke" ? "/revoke" : ""), {
                method: operation === "read" ? "GET" : "POST",
                ...(operation === "read" ? {} : {
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(operation === "issue" ? { action: "grant" }
                        : { grant_id: previous!.grant_id, revision: previous!.revision }),
                }),
                signal: controller.signal, cache: "no-store", redirect: "error",
            });
            const raw: unknown = await response.json();
            if (active.current !== controller) return;
            const parsed = readGrantReceipt(raw, workspaceId, taskId, proposalId);
            if (response.status !== (operation === "issue" ? 201 : 200) || !parsed
                || (operation === "issue" && parsed.grant?.status !== "enabled")
                || (operation === "revoke" && (parsed.grant?.status !== "revoked" || parsed.grant.grant_id !== previous?.grant_id))) {
                throw new Error("unconfirmed");
            }
            // 重新读标记，以免另一个同资源组件已提交新请求。
            const currentMarker = sessionStorage.getItem(guardKey);
            const settled = grantChangeObserved(currentMarker, parsed.grant);
            if (settled) sessionStorage.removeItem(guardKey);
            setReceipt(parsed); setBlocked(!settled);
            setMessage(settled ? "" : "先前变更结果仍未确认，仅可继续查询，不能再次提交。");
        } catch {
            if (active.current !== controller) return;
            setReceipt(null); setBlocked(true);
            setMessage(operation === "read" ? "许可查询失败，请重新查询。" : "许可变更结果未确认，请先查询状态，勿重复提交。");
        } finally {
            if (active.current === controller) { active.current = null; setBusy(false); }
        }
    }

    return <details className="space-y-3" aria-label="普通项目写入许可" onToggle={event => setExpanded(event.currentTarget.open)}>
        <summary className="cursor-pointer font-medium">普通项目写入许可</summary>
        <p className="text-xs text-muted-foreground">许可与提案审批分开。应用前会重新核对文件；请避免其他编辑器同时修改同一文件。</p>
        <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={!available || busy} onClick={() => void run("read")}>{busy ? "正在处理许可…" : "查询许可状态"}</Button>
            {receipt && !blocked && !busy && (receipt.grant === null
                ? <Button size="sm" variant="outline" disabled={!approved} onClick={() => setConfirm("issue")}>发放本提案许可</Button>
                : receipt.grant.status === "enabled" && <Button size="sm" variant="outline" onClick={() => setConfirm("revoke")}>撤销本提案许可</Button>)}
        </div>
        {receipt && <p role="status" className="text-xs">查询时许可：{receipt.grant === null ? "未发放" : receipt.grant.status === "enabled" ? "已启用（不代表当前可写）" : "已撤销，不可重新启用"}</p>}
        {!approved && <p className="text-xs text-muted-foreground">发放前需先审阅并批准提案。</p>}
        {message && <p role="alert" className="text-xs text-destructive">{message}</p>}
        {/* 收起、重新查询或进入许可变更时卸载诊断，旧结果不能跨许可快照沿用。 */}
        {expanded && receipt?.grant && !blocked && !busy && !confirm && <ProjectWriteAssessmentActions
            workspaceId={workspaceId} taskId={taskId} proposalId={proposalId} grant={receipt.grant}
        />}
        {receipt?.grant?.status === "enabled" && approved && !blocked && !busy && <ProjectApplyActions
            workspaceId={workspaceId} taskId={taskId} proposalId={proposalId}
            grantId={receipt.grant.grant_id} revision={receipt.grant.revision}
        />}
        {confirm && <div className="space-y-2 text-xs">
            <p>{confirm === "issue" ? "确认仅为这份提案发放许可？此操作不会应用文件修改。" : "确认撤销这份提案的许可？撤销后不能重新启用。"}</p>
            <Button size="sm" disabled={busy} onClick={() => void run(confirm)}>{confirm === "issue" ? "确认发放许可" : "确认撤销许可"}</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>取消</Button>
        </div>}
    </details>;
}
