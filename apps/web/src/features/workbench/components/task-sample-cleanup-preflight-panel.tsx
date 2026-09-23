"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isProposalIdentifier } from "../file-edit-proposal-data";
import {
    readTaskSampleCleanupPreflight,
    type TaskSampleCleanupPreflight,
} from "../task-sample-cleanup-preflight-data";

type Scope = {
    workspaceId: string;
    taskId: string;
};

type QueryState =
    | { phase: "idle" }
    | { phase: "loading" }
    | { phase: "error" }
    | { phase: "ready"; snapshot: TaskSampleCleanupPreflight };

const labels: Record<TaskSampleCleanupPreflight["result"], string> = {
    evidence_missing: "查询时缺少来源证据",
    not_pending: "查询时来源不处于清理待办",
    evidence_inconsistent: "来源与绑定证据不一致",
    directory_missing: "查询时目录不存在",
    identity_unverifiable: "当前目录身份无法核实",
    identity_matches_record: "当前目录身份与记录一致",
    inspection_unavailable: "当前目录检查暂不可用",
};

const explanations: Record<TaskSampleCleanupPreflight["result"], string> = {
    evidence_missing: "此任务当前没有可供比较的持久来源；不能据此推断先前操作没有副作用。",
    not_pending: "来源记录当前不是清理待办，不能据此恢复或重试文件操作。",
    evidence_inconsistent: "持久记录与当前绑定或目录观察不一致，需要保留现场核查。",
    directory_missing: "这只是查询时刻未看到候选目录，不证明清理已经完成。",
    identity_unverifiable: "旧来源缺少创建时身份，当前目录外观不能证明它是原对象。",
    identity_matches_record: "这只是查询时刻的身份数值匹配，不能据此清理、恢复或重试。",
    inspection_unavailable: "当前无法可靠观察候选目录，不能推断目录是否存在或匹配。",
};

export default function TaskSampleCleanupPreflightPanel(props: Scope) {
    // 资源变化时重建查询状态，不短暂展示上一 Task 的诊断。
    return (
        <CleanupPreflightQuery
            key={`${props.workspaceId}:${props.taskId}`}
            {...props}
        />
    );
}

function CleanupPreflightQuery({ workspaceId, taskId }: Scope) {
    const [state, setState] = useState<QueryState>({ phase: "idle" });
    const active = useRef<AbortController | null>(null);

    useEffect(() => {
        return () => {
            // 卸载先失效引用再取消，迟到响应不能写回新资源界面。
            const controller = active.current;
            active.current = null;
            controller?.abort();
        };
    }, []);

    const available =
        isProposalIdentifier(workspaceId)
        && isProposalIdentifier(taskId);

    function cancel() {
        const controller = active.current;
        active.current = null;
        controller?.abort();
        setState({ phase: "idle" });
    }

    async function load() {
        // ref 立即拦住连续点击，不等待 React 按钮状态更新。
        if (!available || active.current !== null) {
            return;
        }

        const controller = new AbortController();
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(25_000),
        ]);

        active.current = controller;
        setState({ phase: "loading" });

        try {
            const response = await fetch(
                `/api/workspaces/${workspaceId}/tasks/${taskId}/sample-cleanup-preflight`,
                {
                    method: "GET",
                    credentials: "same-origin",
                    cache: "no-store",
                    redirect: "error",
                    signal,
                },
            );

            signal.throwIfAborted();

            if (active.current !== controller) {
                return;
            }

            if (response.status !== 200) {
                // 错误正文不展示；失败不能伪装成缺失或身份匹配。
                await response.body?.cancel();
                signal.throwIfAborted();

                if (active.current === controller) {
                    setState({ phase: "error" });
                }
                return;
            }

            const raw: unknown = await response.json();
            signal.throwIfAborted();

            if (active.current !== controller) {
                return;
            }

            // 浏览器再次检查资源与分类，拒绝 BFF 或缓存中的错资源响应。
            const snapshot = readTaskSampleCleanupPreflight(
                raw,
                workspaceId,
                taskId,
            );

            setState(
                snapshot === null
                    ? { phase: "error" }
                    : { phase: "ready", snapshot },
            );
        } catch {
            // 主动取消回到未查询；超时、网络或协议错误明确显示未知。
            if (
                active.current === controller
                && !controller.signal.aborted
            ) {
                setState({ phase: "error" });
            }
        } finally {
            if (active.current === controller) {
                active.current = null;
            }
        }
    }

    return (
        <section
            aria-label="清理待办只读诊断"
            aria-busy={state.phase === "loading"}
            className="space-y-3 border-b border-border/60 pb-5"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-medium">清理待办诊断</h3>
                <Button
                    type="button"
                    variant="outline"
                    disabled={!available || state.phase === "loading"}
                    onClick={() => void load()}
                >
                    {state.phase === "loading"
                        ? "正在查询"
                        : state.phase === "idle"
                            ? "查询清理诊断"
                            : "重新查询清理诊断"}
                </Button>
            </div>

            {state.phase === "loading" && (
                <Button type="button" variant="ghost" onClick={cancel}>
                    取消查询
                </Button>
            )}

            {!available && (
                <p className="text-sm text-muted-foreground">
                    当前任务信息不完整，无法查询清理诊断。
                </p>
            )}

            {available && state.phase === "idle" && (
                <p className="text-sm text-muted-foreground">
                    尚未查询当前任务的清理诊断。
                </p>
            )}

            {state.phase === "loading" && (
                <p role="status" className="text-sm text-muted-foreground">
                    正在读取诊断快照…
                </p>
            )}

            {state.phase === "error" && (
                <p role="alert" className="text-sm text-destructive">
                    诊断查询失败，当前目录状态未知，请重新查询。
                </p>
            )}

            {state.phase === "ready" && (
                <div role="status" className="space-y-2 text-sm">
                    <p className="font-medium">
                        {labels[state.snapshot.result]}
                    </p>
                    <p className="text-muted-foreground">
                        {explanations[state.snapshot.result]}
                    </p>
                </div>
            )}

            <p className="text-sm text-muted-foreground">
                查询不会清理、恢复或执行样例；结果只反映查询时刻。
            </p>
        </section>
    );
}
