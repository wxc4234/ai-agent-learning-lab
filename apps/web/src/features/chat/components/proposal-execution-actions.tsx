"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
    createProposalExecutionRequest,
    type ProposalExecutionRequestState,
    type ProposalExecutionScope,
} from "../../workbench/proposal-execution-request";
import ProposalExecutionResultPanel from "./proposal-execution-result";

type ViewState = ProposalExecutionRequestState | { phase: "checking" | "unavailable" };

// 用资源标识作为组件生命周期边界，避免新资源短暂显示旧回执或确认勾选。
export default function ProposalExecutionActions(scope: ProposalExecutionScope) {
    return <ExecutionSession key={`${scope.workspaceId}:${scope.taskId}:${scope.proposalId}`} {...scope} />;
}

function ExecutionSession({ workspaceId, taskId, proposalId }: ProposalExecutionScope) {
    const [state, setState] = useState<ViewState>({ phase: "checking" });
    const [confirmed, setConfirmed] = useState(false);
    const modelRef = useRef<ReturnType<typeof createProposalExecutionRequest> | null>(null);

    useEffect(() => {
        let active = true;
        let unsubscribe = () => {};
        let model: ReturnType<typeof createProposalExecutionRequest> | null = null;
        try {
            // 只在客户端effect中读取真实sessionStorage，避免SSR访问window。
            model = createProposalExecutionRequest({
                storage: window.sessionStorage,
                fetch: (input, init) => window.fetch(input, init),
            });
            model.select({ workspaceId, taskId, proposalId });
            modelRef.current = model;
            const current = model;
            const update = () => {
                if (active) setState(current.getState());
            };
            unsubscribe = model.subscribe(update);
            // 首次服务端与客户端均显示checking，挂载后再展示存储结果。
            queueMicrotask(update);
        } catch {
            queueMicrotask(() => {
                if (active) setState({ phase: "unavailable" });
            });
        }
        return () => {
            // 先解除订阅再销毁，避免取消时更新已经卸载的React组件。
            active = false;
            unsubscribe();
            model?.dispose();
            if (modelRef.current === model) modelRef.current = null;
        };
    }, [workspaceId, taskId, proposalId]);

    const canSubmit = state.phase === "idle" && confirmed;

    return (
        <section aria-label="受限样例提案应用" className="space-y-3 border-t border-border pt-3 text-sm">
            <p className="font-medium">应用到服务端样例</p>
            <p className="text-muted-foreground">
                仅服务端登记的临时样例可执行。批准提案或勾选确认不代表获得任意项目文件写入权限。
            </p>
            {state.phase === "idle" && (
                <label className="flex items-start gap-2">
                    <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) => setConfirmed(event.target.checked)}
                    />
                    <span>我确认应用此样例提案；停止等待不会撤销文件修改。</span>
                </label>
            )}
            <div className="flex flex-wrap gap-2">
                <Button
                    type="button"
                    size="sm"
                    disabled={!canSubmit}
                    onClick={() => {
                        if (canSubmit) void modelRef.current?.submit();
                    }}
                >
                    应用样例提案
                </Button>
                {state.phase === "submitting" && (
                    <Button type="button" size="sm" variant="outline" onClick={() => modelRef.current?.cancel()}>
                        停止等待
                    </Button>
                )}
            </div>
            {state.phase === "checking" && <p role="status">正在检查本标签页的提交记录…</p>}
            {state.phase === "submitting" && (
                <p role="status">正在等待应用结果。停止等待、收起组件或切换任务均不会撤销写入。</p>
            )}
            {state.phase === "uncertain" && (
                <p role="alert" className="text-destructive">
                    本次执行结果未确认，不能据此判断文件未修改。已阻止再次提交，请保留现场核对。
                </p>
            )}
            {state.phase === "unavailable" && (
                <p role="alert" className="text-destructive">无法安全读取或保存提交记录，暂不能应用样例。</p>
            )}
            {state.phase === "receipt" && (
                <ProposalExecutionResultPanel
                    workspaceId={workspaceId}
                    taskId={taskId}
                    proposalId={proposalId}
                    receipt={state.receipt}
                />
            )}
            <p className="text-xs text-muted-foreground">
                本标签页提交记录不会自动解除；清除浏览器记录不等于恢复安全执行条件。
            </p>
        </section>
    );
}
