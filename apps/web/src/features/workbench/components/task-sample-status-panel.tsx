"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isProposalIdentifier } from "../file-edit-proposal-data";
import {
    readTaskSampleStatus,
    type TaskSampleStatus,
} from "../task-sample-status-data";

type Scope = {
    workspaceId: string;
    taskId: string;
};

type QueryState =
    | { phase: "idle" }
    | { phase: "loading" }
    | { phase: "error" }
    | {
        phase: "ready";
        snapshot: TaskSampleStatus;
    };

const labels: Record<TaskSampleStatus["status"], string> = {
    missing: "查询时没有该任务的进程内样例登记",
    busy: "查询时样例正在使用",
    sealed: "查询时登记已封锁或绑定不匹配",
    ready: "查询时登记可供后续门禁检查",
};

const explanations: Record<TaskSampleStatus["status"], string> = {
    missing: "不能据此推断先前执行没有副作用，也不能清除提交保护。",
    busy: "这是查询时刻的占用快照，不表示可以再次执行。",
    sealed: "不能自动恢复登记或重试提案应用。",
    ready: "真正应用时仍须重新授权并核对样例；此状态不预留执行权。",
};

export default function TaskSampleStatusPanel(props: Scope) {
    // 资源变化时重建状态，避免新任务短暂显示旧任务的快照。
    return (
        <SampleStatusQuery
            key={`${props.workspaceId}:${props.taskId}`}
            {...props}
        />
    );
}

function SampleStatusQuery({ workspaceId, taskId }: Scope) {
    const [state, setState] = useState<QueryState>({ phase: "idle" });
    const active = useRef<AbortController | null>(null);

    useEffect(() => {
        return () => {
            // 卸载时先失效引用，再取消请求；迟到结果不能更新界面。
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
        // ref 立即阻止连续点击在React更新按钮前发出重复请求。
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
                `/api/workspaces/${workspaceId}/tasks/${taskId}/sample-status`,
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
                // 错误正文不参与显示；失败不能推断为missing。
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

            // 浏览器再次核对资源、状态及封锁原因的允许组合。
            const snapshot = readTaskSampleStatus(
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
            // 主动取消保持静默；超时、网络和协议错误显示查询失败。
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
            aria-label="受限样例登记状态"
            aria-busy={state.phase === "loading"}
            className="space-y-3 border-b border-border/60 pb-5"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-medium">样例登记状态</h3>
                <Button
                    type="button"
                    variant="outline"
                    disabled={!available || state.phase === "loading"}
                    onClick={() => void load()}
                >
                    {state.phase === "loading"
                        ? "正在查询"
                        : state.phase === "idle"
                            ? "查询登记状态"
                            : "重新查询登记状态"}
                </Button>
            </div>

            {state.phase === "loading" && (
                <Button
                    type="button"
                    variant="ghost"
                    onClick={cancel}
                >
                    取消查询
                </Button>
            )}

            {!available && (
                <p className="text-sm text-muted-foreground">
                    当前任务信息不完整，无法查询登记状态。
                </p>
            )}

            {available && state.phase === "idle" && (
                <p className="text-sm text-muted-foreground">
                    尚未查询当前任务的样例登记。
                </p>
            )}

            {state.phase === "loading" && (
                <p role="status" className="text-sm text-muted-foreground">
                    正在读取登记快照…
                </p>
            )}

            {state.phase === "error" && (
                <p role="alert" className="text-sm text-destructive">
                    登记状态查询失败，当前状态未知，请重新查询。
                </p>
            )}

            {state.phase === "ready" && (
                <div role="status" className="space-y-2 text-sm">
                    <p className="font-medium">
                        {state.snapshot.sealed_reason === "cleanup_pending"
                            ? "查询时样例登记处于清理待办"
                            : labels[state.snapshot.status]}
                    </p>
                    <p className="text-muted-foreground">
                        {state.snapshot.sealed_reason === "cleanup_pending"
                            ? "这是持久化登记的清理待办状态；不能据此判断样例目录是否仍存在，也不能自动恢复登记或重试提案应用。"
                            : explanations[state.snapshot.status]}
                    </p>
                </div>
            )}

            <p className="text-sm text-muted-foreground">
                查询不会创建、关闭或执行样例；结果只反映查询时刻。
            </p>
        </section>
    );
}
