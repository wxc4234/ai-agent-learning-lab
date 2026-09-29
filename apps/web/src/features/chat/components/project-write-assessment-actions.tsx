"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { assessmentLabels, readAssessmentResult, type AssessmentResult } from "../../workbench/project-write-assessment-data";
import type { GrantRecord } from "../../workbench/project-write-grant-data";

type Props = { workspaceId: string; taskId: string; proposalId: string; grant: GrantRecord };

export default function ProjectWriteAssessmentActions(props: Props) {
    // 许可修订与资源共同标识一次诊断上下文，不能沿用旧上下文的结果。
    return <AssessmentActions key={`${props.workspaceId}:${props.taskId}:${props.proposalId}:${props.grant.grant_id}:${props.grant.revision}`} {...props} />;
}

function AssessmentActions({ workspaceId, taskId, proposalId, grant }: Props) {
    const [result, setResult] = useState<AssessmentResult | null>(null);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => {
        // 先使旧实例失效，即便网络层未及时中止，迟到响应也不会更新界面。
        const controller = active.current;
        active.current = null;
        controller?.abort();
    }, []);

    function cancel() {
        const controller = active.current;
        active.current = null;
        controller?.abort();
        setBusy(false);
        setResult(null);
        setMessage("检查已取消，当前结果未知。");
    }

    async function assess() {
        if (active.current) return;
        const controller = new AbortController();
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]);
        active.current = controller;
        setBusy(true); setResult(null); setMessage("");
        try {
            const response = await fetch(
                `/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/assessment`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    // 这是本次检查的拟应用意图，不提交应用，也不领取应用机会。
                    body: JSON.stringify({ grant_id: grant.grant_id, revision: grant.revision, apply_requested: true }),
                    signal, cache: "no-store", redirect: "error",
                },
            );
            signal.throwIfAborted();
            const raw: unknown = await response.json();
            signal.throwIfAborted();
            if (active.current !== controller) return;
            const parsed = readAssessmentResult(raw, workspaceId, taskId, proposalId);
            if (response.status !== 200 || parsed === null) throw new Error("assessment_unconfirmed");
            setResult(parsed);
        } catch {
            if (active.current !== controller) return;
            setResult(null);
            setMessage("检查未取得可信结果，当前结果未知。可手动重新检查。");
        } finally {
            if (active.current === controller) { active.current = null; setBusy(false); }
        }
    }

    return <section aria-label="写入条件检查" className="space-y-2 border-t pt-3">
        <p className="text-xs text-muted-foreground">仅检查当前条件，不会应用修改。结果是检查时的快照，不代表获得写入权限。</p>
        <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void assess()}>
                {busy ? "正在检查…" : "检查写入条件"}
            </Button>
            {busy && <Button size="sm" variant="ghost" onClick={cancel}>取消检查</Button>}
        </div>
        {busy && <p role="status" className="text-xs">正在检查写入条件…</p>}
        {result && <p role="status" className="text-xs">检查结果：{assessmentLabels[result]}。</p>}
        {message && <p role="alert" className="text-xs text-destructive">{message}</p>}
    </section>;
}
