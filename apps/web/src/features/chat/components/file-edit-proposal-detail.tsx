"use client";

import { useEffect, useRef, useState } from "react";
import {
    isProposalIdentifier,
    readFileEditProposalDetail,
    type FileEditProposalDetail,
} from "../../workbench/file-edit-proposal-data";
import { Button } from "@/components/ui/button";
import FileEditProposalActions from "./file-edit-proposal-actions";
import ProposalApplicationStatusPanel from "./proposal-application-status";

type DetailState =
    | { status: "idle" }
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; detail: FileEditProposalDetail };

export default function FileEditProposalDetailPanel({
    workspaceId,
    taskId,
    proposalId,
}: {
    workspaceId: string;
    taskId: string;
    proposalId: string;
}) {
    const [state, setState] = useState<DetailState>({
        status: "idle",
    });
    const controllerRef = useRef<AbortController | null>(null);

    // 父组件按项目/任务/提案设置key；目标变化会卸载旧实例。
    // abort减少无用请求，清空引用同时阻止旧响应更新状态。
    useEffect(() => {
        return () => {
            controllerRef.current?.abort();
            controllerRef.current = null;
        };
    }, []);

    const available = [workspaceId, taskId, proposalId].every(
        isProposalIdentifier,
    );

    function closeDetail() {
        controllerRef.current?.abort();
        controllerRef.current = null;
        setState({ status: "idle" });
    }

    async function loadDetail() {
        if (!available) {
            return;
        }

        // 新请求取代旧请求；结果必须属于当前controller才允许展示。
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;
        setState({ status: "loading" });

        try {
            const response = await fetch(
                `/api/workspaces/${workspaceId}/tasks/${taskId}`
                    + `/file-edit-proposals/${proposalId}`,
                {
                    method: "GET",
                    signal: controller.signal,
                    cache: "no-store",
                    redirect: "error",
                },
            );

            if (
                controller.signal.aborted
                || controllerRef.current !== controller
            ) {
                return;
            }

            if (!response.ok) {
                // 不展示上游正文，只根据允许的状态生成固定提示。
                await response.body?.cancel();
                throw new Error(
                    response.status === 404
                        ? "提案不存在或不可访问"
                        : "提案详情读取失败，请重试",
                );
            }

            const raw: unknown = await response.json();

            if (
                controller.signal.aborted
                || controllerRef.current !== controller
            ) {
                return;
            }

            // 浏览器再次验证公开协议及目标，不能只相信JSON可解析。
            const detail = readFileEditProposalDetail(
                raw,
                workspaceId,
                taskId,
                proposalId,
            );

            if (detail === null) {
                setState({
                    status: "error",
                    message: "提案详情格式不符合要求，请重试",
                });
                return;
            }

            setState({ status: "ready", detail });
        } catch (error) {
            if (
                controller.signal.aborted
                || controllerRef.current !== controller
            ) {
                return;
            }

            // 只保留本组件生成的404提示，网络异常正文不进入页面。
            const notAccessible = (
                error instanceof Error
                && error.message === "提案不存在或不可访问"
            );

            setState({
                status: "error",
                message: notAccessible
                    ? "提案不存在或不可访问"
                    : "提案详情读取失败，请重试",
            });
        } finally {
            if (controllerRef.current === controller) {
                controllerRef.current = null;
            }
        }
    }

    if (!available) {
        return (
            <p className="mt-3 text-xs text-muted-foreground">
                当前任务信息不完整，无法读取提案详情
            </p>
        );
    }

    return (
        <section
            aria-label="文件修改提案详情"
            className="mt-3 min-w-0 space-y-3 border-t border-border pt-3"
        >
            <div className="flex flex-wrap gap-2">
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={state.status === "loading"}
                    onClick={() => void loadDetail()}
                >
                    {state.status === "loading"
                        ? "正在读取"
                        : state.status === "error"
                            ? "重试读取详情"
                            : state.status === "ready"
                                ? "重新读取详情"
                                : "查看提案详情"}
                </Button>

                {state.status !== "idle" && (
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={closeDetail}
                    >
                        {state.status === "loading"
                            ? "取消读取"
                            : "收起详情"}
                    </Button>
                )}
            </div>

            {state.status === "loading" && (
                <p role="status" className="text-xs text-muted-foreground">
                    正在读取已保存的提案…
                </p>
            )}

            {state.status === "error" && (
                <p role="alert" className="text-xs text-destructive">
                    {state.message}
                </p>
            )}

            {state.status === "ready" && (
                <div className="min-w-0 space-y-3 text-xs">
                    <p className="font-medium">
                        查询时状态：
                        {state.detail.status === "pending"
                            ? "待审批"
                            : state.detail.status === "approved"
                                ? "已批准"
                                : "已拒绝"}
                    </p>
                    <p className="break-all font-mono">
                        {state.detail.relative_path}
                    </p>

                    <details>
                        <summary className="cursor-pointer text-muted-foreground">
                            详情内容摘要 SHA-256
                        </summary>
                        <dl className="mt-2 space-y-2">
                            <div>
                                <dt>原文基线</dt>
                                <dd className="break-all font-mono">
                                    {state.detail.baseline_sha256}
                                </dd>
                            </div>
                            <div>
                                <dt>提案内容</dt>
                                <dd className="break-all font-mono">
                                    {state.detail.proposed_sha256}
                                </dd>
                            </div>
                        </dl>
                    </details>

                    <p className="text-muted-foreground">
                        {state.detail.diff_truncated
                            ? "Diff 已截断，审阅内容不完整"
                            : "保存的 Diff 未截断"}
                    </p>

                    {/* 使用纯文本，不将文件内容当HTML或Markdown执行。 */}
                    <pre
                        aria-label="提案 Diff"
                        tabIndex={0}
                        className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-background p-2 font-mono text-xs"
                    >
                        {state.detail.diff}
                    </pre>

                    <p className="text-muted-foreground">
                        这是保存的审阅内容，不代表当前文件仍符合基线。
                        Diff 仅供审阅，不能直接用于 git apply。
                        读取详情不会改变审批状态；批准也不会直接写入文件。
                    </p>

                    <ProposalApplicationStatusPanel
                        workspaceId={state.detail.workspace_id}
                        taskId={state.detail.task_id}
                        proposalId={state.detail.proposal_id}
                    />

                    <FileEditProposalActions
                        key={
                            `${state.detail.workspace_id}:`
                            + `${state.detail.task_id}:`
                            + state.detail.proposal_id
                        }
                        detail={state.detail}
                        onDecided={(decision) => {
                            // 审批组件只在当前ready分支挂载。
                            // 已核对的成功回执更新当前快照，不另发写请求。
                            setState((previous) => {
                                if (previous.status !== "ready") {
                                    return previous;
                                }

                                return {
                                    status: "ready",
                                    detail: {
                                        ...previous.detail,
                                        status: decision,
                                    },
                                };
                            });
                        }}
                    />
                </div>
            )}
        </section>
    );
}
