"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isProposalIdentifier } from "../../workbench/file-edit-proposal-data";
import {
    readProposalApplicationStatus,
    type ProposalApplicationStatus,
} from "../../workbench/proposal-application-status-data";

type Scope = { workspaceId: string; taskId: string; proposalId: string };
type State =
    | { phase: "idle" }
    | { phase: "loading" }
    | { phase: "error"; message: string }
    | { phase: "ready"; status: ProposalApplicationStatus["application_status"] };

const labels = {
    idle: "尚未领取执行",
    running: "已领取，尚无终态记录",
    applied: "已登记应用成功",
    not_applied: "已登记本次未应用",
    uncertain: "结果或清理未确认",
};
const explanations = {
    idle: "尚未领取不代表当前具备执行条件。",
    running: "不保证执行进程仍在运行，不能据此再次执行。",
    applied: "这是已登记的结果，不保证文件后来没有被修改。",
    not_applied: "本次执行机会已消耗，不能重新领取。",
    uncertain: "继续保留资源保护，不自动重试或释放占用。",
};

export default function ProposalApplicationStatusPanel(props: Scope) {
    // 将身份变化直接转换成卸载，旧状态不会短暂出现在新提案下。
    return <StatusQuery key={`${props.workspaceId}:${props.taskId}:${props.proposalId}`} {...props} />;
}

function StatusQuery({ workspaceId, taskId, proposalId }: Scope) {
    const [state, setState] = useState<State>({ phase: "idle" });
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => {
        active.current?.abort();
        active.current = null;
    }, []);

    const available = [workspaceId, taskId, proposalId].every(isProposalIdentifier);
    function cancel() {
        active.current?.abort();
        active.current = null;
        setState({ phase: "idle" });
    }
    async function load() {
        if (!available || active.current) return;
        const controller = new AbortController();
        active.current = controller;
        setState({ phase: "loading" });
        try {
            const response = await fetch(
                `/api/workspaces/${workspaceId}/tasks/${taskId}`
                    + `/file-edit-proposals/${proposalId}/application-status`,
                { method: "GET", signal: controller.signal, cache: "no-store", redirect: "error" },
            );
            if (controller.signal.aborted || active.current !== controller) return;
            if (response.status !== 200) {
                await response.body?.cancel();
                if (controller.signal.aborted || active.current !== controller) return;
                setState({ phase: "error", message: response.status === 404
                    ? "提案不存在或不可访问"
                    : "应用状态读取失败，请重新查询" });
                return;
            }
            const raw: unknown = await response.json();
            if (controller.signal.aborted || active.current !== controller) return;
            const result = readProposalApplicationStatus(raw, workspaceId, taskId, proposalId);
            setState(result === null
                ? { phase: "error", message: "应用状态格式不符合要求，请重新查询" }
                : { phase: "ready", status: result.application_status });
        } catch {
            if (controller.signal.aborted || active.current !== controller) return;
            // 网络和解析异常不展示原文，不将失败降级为idle。
            setState({ phase: "error", message: "应用状态读取失败，请重新查询" });
        } finally {
            if (active.current === controller) active.current = null;
        }
    }

    return (
        <section aria-label="提案应用状态" className="space-y-2 border-t border-border pt-3">
            <p className="font-medium">应用记录</p>
            <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm"
                    disabled={!available || state.phase === "loading"} onClick={() => void load()}>
                    {state.phase === "loading" ? "正在查询应用状态"
                        : state.phase === "idle" ? "查询应用状态" : "重新查询应用状态"}
                </Button>
                {state.phase === "loading" && (
                    <Button type="button" variant="ghost" size="sm" onClick={cancel}>取消状态查询</Button>
                )}
            </div>
            {!available && <p className="text-muted-foreground">任务信息不完整，无法查询应用状态</p>}
            {state.phase === "loading" && <p role="status">正在读取应用记录…</p>}
            {state.phase === "error" && <p role="alert" className="text-destructive">{state.message}</p>}
            {state.phase === "ready" && (
                <div role="status" className="space-y-1">
                    <p>查询时应用状态：{labels[state.status]}</p>
                    <p className="text-muted-foreground">{explanations[state.status]}</p>
                </div>
            )}
            <p className="text-muted-foreground">仅查询已保存的记录，不执行文件修改，也不恢复执行。</p>
        </section>
    );
}
