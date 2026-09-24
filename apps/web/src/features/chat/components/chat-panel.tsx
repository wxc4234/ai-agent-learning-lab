"use client";

import {
    useEffect,
    useLayoutEffect,
    useReducer,
    useRef,
    useState,
    type SubmitEvent,
} from "react";
import { formatToolDuration } from "../tool-duration-view";

import { readAgentStream } from "../agent-stream";
import {
    chatReducer,
    initialChatState,
    toUserFacingError,
} from "../chat-state";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import WorkbenchShell, { WorkbenchDetails } from "@/features/workbench/components/workbench-shell";
import WorkbenchIcon from "@/features/workbench/components/workbench-icon";
import LoadingPlaceholder from "@/features/workbench/components/loading-placeholder";
import TaskRunPanel from '@/features/workbench/components/task-run-panel';
import ConversationExecutionPanel from '@/features/workbench/components/conversation-execution-panel';
import ToolResult from "./tool-result";
import { useRestoredRunSummary } from "@/features/workbench/use-restored-run-summary";
import RunMetricsFooter from "./run-metrics-footer";
import TaskChangesPanel from "@/features/workbench/components/task-changes-panel";
import MarkdownMessage from "./markdown-message";
import TaskSampleStatusPanel from "@/features/workbench/components/task-sample-status-panel";
import TaskSampleCleanupPreflightPanel from "@/features/workbench/components/task-sample-cleanup-preflight-panel";

import {
    WorkbenchProvider,
    useWorkbench,
} from "@/features/workbench/workbench-session";
import {
    readTask,
    record,
    readMessages,
    type HistoryMessage,
} from "@/features/workbench/task-data";

const REQUEST_TIMEOUT_MS = 30_000;
type CancelReason = "user" | "timeout";

function statusLabel(status: string): string {
    switch (status) {
        case "thinking":
            return "正在思考……";
        case "streaming":
            return "正在生成……";
        case "done":
            return "已完成";
        case "aborted":
            return "已停止生成";
        case "error":
            return "生成失败";
        default:
            return "等待提问";
    }
}

export default function ChatPanel({
    localMode = true,
}: {
    localMode?: boolean;
}) {
    return (
        <WorkbenchProvider localMode={localMode}>
            <WorkbenchShell>
                <SelectedChat />
            </WorkbenchShell>
        </WorkbenchProvider>
    );
}

function SelectedChat() {
    const {
        selection,
        localMode,
        restoreStatus,
        restoreError,
        retryRestore,
    } = useWorkbench();

    if (localMode && restoreStatus !== 'ready') {
        return (
            <div className="flex flex-1 items-center justify-center px-6">
                {restoreStatus === 'loading' ? (
                    <p role="status" className="text-base text-muted-foreground">
                        正在恢复任务…
                    </p>
                ) : (
                    <div role="alert" className="max-w-xl space-y-4">
                        <p className="text-base">
                            {restoreError}
                        </p>
                        <Button variant="outline" onClick={retryRestore}>
                            重新读取任务
                        </Button>
                    </div>
                )}
            </div>
        );
    }

    // 详情成功后才挂载 TaskChat；它继续负责历史读取及发送限制。
    return <TaskChat key={selection?.key ?? 'empty'} />;
}

function TaskChat() {
    const workbench = useWorkbench();
    const initialTask = useRef(workbench.selection?.task ?? null);
    const currentTaskRef = useRef(initialTask.current);
    const [history, setHistory] = useState<HistoryMessage[]>([]);
    const conversationRef = useRef<HTMLElement | null>(null);
    const currentTurnRef = useRef<HTMLDivElement | null>(null);
    const [turnPositionVersion, positionNewTurn] = useReducer(value => value + 1, 0);

    useLayoutEffect(() => {
        const viewport = conversationRef.current;
        const turn = currentTurnRef.current;
        if (!viewport || !turn || !turnPositionVersion) return;

        // 预留一屏高度，让短回复的问题也能停在顶部。
        // 仅发送/重试时定位，文本增量和终态不抢走用户的阅读位置。
        const reserveSpace = () => {
            const styles = getComputedStyle(viewport);
            turn.style.minHeight = `${Math.max(0, viewport.clientHeight
                - parseFloat(styles.paddingTop) - parseFloat(styles.paddingBottom))}px`;
        };
        reserveSpace();
        viewport.scrollTop += turn.getBoundingClientRect().top
            - viewport.getBoundingClientRect().top
            - parseFloat(getComputedStyle(viewport).paddingTop);
        const observer = new ResizeObserver(reserveSpace);
        observer.observe(viewport);
        return () => observer.disconnect();
    }, [turnPositionVersion]);

    const [historyLoading, setHistoryLoading] = useState(
        Boolean(initialTask.current),
    );
    const [historyError, setHistoryError] = useState(false);
    const [historyRetry, setHistoryRetry] = useState(0);
    const [creating, setCreating] = useState(false);
    const savedIntent = useRef(workbench.creationIntent());
    const [creationError, setCreationError] = useState<string | null>(
        savedIntent.current ? "这份草稿的创建结果尚未确认，请重试找回任务。" : null,
    );
    const [uncertain, setUncertain] = useState(Boolean(savedIntent.current));
    const [creationBlocked, setCreationBlocked] = useState(false);
    const creationControllerRef = useRef<AbortController | null>(null);
    const creationRef = useRef(false);
    const mountedRef = useRef(true);
    const [prompt, setPrompt] = useReducer(
        (_current: string, next: string) => next,
        savedIntent.current?.prompt ?? "",
    );
    const [chatState, dispatch] = useReducer(chatReducer, initialChatState);
    const restoredMetrics = useRestoredRunSummary(initialTask.current, chatState.status === "idle");
    const restoredSummary = restoredMetrics.summary;
    const [activeRunId, setActiveRunId] = useState<string | null>(null);
    const [cancellationNotice, setCancellationNotice] = useState<string | null>(
        null,
    );
    const requestVersionRef = useRef(0);

    const controllerRef = useRef<AbortController | null>(null);
    const sessionIdRef = useRef<string | null>(
        initialTask.current?.conversation_id ?? null,
    );
    const activeRunIdRef = useRef<string | null>(null);
    const cancellationPendingRef = useRef(false);

    const isBusy =
        chatState.status === "thinking" || chatState.status === "streaming";

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            controllerRef.current?.abort();
            creationControllerRef.current?.abort();
        };
    }, []);
    useEffect(() => {
        const task = initialTask.current;
        if (!task) return;
        const controller = new AbortController();
        void (async () => {
            try {
                const response = await fetch(
                    `/api/workspaces/${task.workspace_id}/tasks/${task.external_id}/messages`,
                    {
                        cache: "no-store",
                        signal: AbortSignal.any([
                            controller.signal,
                            AbortSignal.timeout(10000),
                        ]),
                    },
                );
                const messages = readMessages(await response.json());
                if (!response.ok || !messages) throw new Error();
                if (!controller.signal.aborted) {
                    setHistory(messages);
                    setHistoryError(false);
                }
            } catch {
                if (!controller.signal.aborted) setHistoryError(true);
            } finally {
                if (!controller.signal.aborted) setHistoryLoading(false);
            }
        })();
        return () => controller.abort();
    }, [historyRetry]);

    async function prepareTask(requestPrompt: string, retry = false): Promise<boolean> {
        if (sessionIdRef.current) return true;
        const workspace = workbench.selection?.workspace;
        if (!workspace || creationRef.current || (!retry && uncertain)) return false;
        const intent = workbench.beginCreation(requestPrompt);
        if (!intent) return false;
        creationRef.current = true;
        setCreating(true);
        workbench.setBusy(true);
        setCreationError(null);
        const controller = new AbortController();
        creationControllerRef.current = controller;
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
        const current = () => mountedRef.current && !controller.signal.aborted;
        try {
            const response = await fetch(
                `/api/workspaces/${workspace.external_id}/tasks`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    // 重试使用首次保存的标题和键，不读取后来的输入或重新生成键。
                    body: JSON.stringify({ title: intent.title, request_key: intent.requestKey }),
                    cache: "no-store",
                    signal,
                },
            );
            const payload: unknown = await response.json();
            signal.throwIfAborted();
            if (!current()) return false;
            const task = response.status === 201 ? readTask(payload, workspace.external_id) : null;
            if (!task) {
                const code = record(payload) ? payload.code : null;
                if (response.status === 409 && (
                    code === "task_creation_conflict" || code === "task_creation_result_deleted"
                )) {
                    setCreationBlocked(true);
                    setUncertain(true);
                    setCreationError(code === "task_creation_conflict"
                        ? "这次创建的内容与已保存记录冲突。请查看任务列表，或明确开始另一项任务。"
                        : "这次创建对应的任务已删除。旧请求不会重建它，可以开始另一项任务。");
                    return false;
                }
                // 包括明确拒绝也保留原键：之前一次请求可能已经成功提交。
                throw new Error();
            }
            workbench.clearCreation();
            sessionIdRef.current = task.conversation_id;
            currentTaskRef.current = task;
            setUncertain(false);
            workbench.adopt(task);
            if (retry) {
                // 找回任务只确认创建结果，不代表消息尚未发送；先读历史，等待明确发送。
                initialTask.current = task;
                setHistoryLoading(true);
                setHistoryRetry(value => value + 1);
                setCreationError("任务已找回。请查看历史并确认输入，再点击发送；本次没有自动发送消息。");
            }
            return true;
        } catch {
            if (current()) {
                setUncertain(true);
                setCreationError("创建结果未确认或请求被拒绝。可以重试同一次创建，不会自动发送消息。");
                workbench.refresh();
            }
            return false;
        } finally {
            creationRef.current = false;
            if (current()) {
                setCreating(false);
                workbench.setBusy(false);
            }
        }
    }

    async function retryCreation() {
        const intent = workbench.creationIntent();
        if (intent && !creationBlocked) await prepareTask(intent.prompt, true);
    }

    function startAnotherTask() {
        const workspace = workbench.selection?.workspace;
        if (!workspace || creationRef.current) return;
        workbench.clearCreation();
        workbench.select(workspace);
    }

    async function summarizeTitle() {
        const task = currentTaskRef.current;
        if (!task) {
            workbench.refresh();
            return;
        }
        try {
            const response = await fetch(
                `/api/workspaces/${task.workspace_id}/tasks/${task.external_id}/title`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                    signal: AbortSignal.timeout(20000),
                },
            );
            const data = await response.json();
            if (
                response.ok &&
                typeof data.title === "string" &&
                data.title.trim() &&
                Array.from(data.title).length <= 200
            )
                workbench.rename(task.external_id, data.title);
        } catch {
            /* 总结失败保留临时标题，不影响完成的对话。 */
        }
        workbench.refresh();
    }

    async function startRequest(requestPrompt: string, isRetry = false) {
        if (controllerRef.current) return;
        workbench.setBusy(true);
        setCreationError(null);
        setPrompt("");
        if (
            !isRetry &&
            chatState.lastPrompt &&
            chatState.reply &&
            chatState.status === "done"
        ) {
            setHistory((old) => [
                ...old,
                { role: "user", content: chatState.lastPrompt },
                { role: "assistant", content: chatState.reply },
            ]);
        }
        positionNewTurn();
        requestVersionRef.current += 1;
        setCancellationNotice(null);
        activeRunIdRef.current = null;
        cancellationPendingRef.current = false;
        setActiveRunId(null);
        const controller = new AbortController();
        let didTimeout = false;
        const timeoutId = window.setTimeout(() => {
            didTimeout = true;
            void requestCancellation("timeout");
        }, REQUEST_TIMEOUT_MS);

        controllerRef.current = controller;
        if (isRetry) {
            dispatch({ type: "retry" });
        } else {
            dispatch({ type: "submit", prompt: requestPrompt });
        }

        try {
            const res = await fetch("/api/chat/stream", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    session_id: sessionIdRef.current,
                    prompt: requestPrompt,
                }),
                signal: controller.signal,
            });

            if (!mountedRef.current) return;
            const runId = res.headers.get("X-Run-ID");
            activeRunIdRef.current = runId;
            setActiveRunId(runId);
            if (!res.ok) {
                throw res;
            }
            if (!res.body) {
                throw new Error("服务端没有返回流式内容");
            }

            for await (const event of readAgentStream(res.body)) {
                if (!mountedRef.current) return;

                const isTerminalEvent =
                    event.type === "RUN_ERROR" || event.type === "RUN_FINISHED";
                // 取消已经发起时，保留用户停止或超时的语义。
                // 交给下方现有的 AbortError 分支完成状态更新。
                if (isTerminalEvent && cancellationPendingRef.current) {
                    throw new DOMException("请求已取消", "AbortError");
                }

                switch (event.type) {
                    case "TOOL_CALL_START":
                        dispatch({
                            type: "tool-start",
                            toolCallId: event.tool_call_id,
                            toolName: event.tool_name,
                            arguments: event.arguments,
                        });
                        break;
                    case "TOOL_CALL_RESULT":
                        dispatch({
                            type: "tool-result",
                            toolCallId: event.tool_call_id,
                            result: event.result,
                            durationMs: event.duration_ms,
                        });
                        break;
                    case "TOOL_CALL_ERROR":
                        dispatch({
                            type: "tool-error",
                            toolCallId: event.tool_call_id,
                            message: event.message,
                            durationMs: event.duration_ms,
                        });
                        break;
                    case "TEXT_MESSAGE_START":
                        dispatch({ type: "stream-start" });
                        break;
                    case "TEXT_MESSAGE_CONTENT":
                        if (!cancellationPendingRef.current) {
                            dispatch({ type: "append", chunk: event.chunk });
                        }
                        break;
                    case "TEXT_MESSAGE_END":
                        break;
                    case "RUN_FINISHED":
                        void summarizeTitle();
                        if (!cancellationPendingRef.current) {
                            dispatch({
                                type: "complete",
                                stepsTaken: event.steps_taken,
                                metrics: event.metrics,
                            });
                        }

                        return;
                    case "RUN_ERROR":
                        if (
                            event.steps_taken !== undefined &&
                            event.metrics !== undefined
                        ) {
                            dispatch({
                                type: "fail",
                                message: event.message,
                                summary: {
                                    stepsTaken: event.steps_taken,
                                    metrics: event.metrics,
                                },
                            });
                        } else {
                            dispatch({
                                type: "fail",
                                message: event.message,
                            });
                        }
                        return;
                }
            }
            // 能走到这里，说明流已经结束，但没有收到运行终态。
            if (cancellationPendingRef.current) {
                throw new DOMException("请求已取消", "AbortError");
            }

            throw new Error("响应意外中断，请重试。");
        } catch (error) {
            if (!mountedRef.current) return;
            if (error instanceof DOMException && error.name === "AbortError") {
                if (didTimeout) {
                    dispatch({
                        type: "fail",
                        message: "请求超时了，请稍后重试。",
                    });
                } else {
                    dispatch({ type: "abort" });
                }
                return;
            }

            dispatch({ type: "fail", message: toUserFacingError(error) });
        } finally {
            window.clearTimeout(timeoutId);
            workbench.setBusy(false);
            if (controllerRef.current === controller) {
                controllerRef.current = null;
            }
        }
    }

    async function requestCancellation(reason: CancelReason) {
        if (cancellationPendingRef.current || controllerRef.current === null) {
            return;
        }

        cancellationPendingRef.current = true;
        const runId = activeRunIdRef.current;
        const controller = controllerRef.current;
        const requestVersion = requestVersionRef.current;
        const cancellationController = new AbortController();

        const cancellationTimeoutId = window.setTimeout(() => {
            cancellationController.abort();
        }, 5_000);

        let notice: string | null = null;

        try {
            if (runId) {
                const response = await fetch(`/api/runs/${runId}/cancel`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ reason }),
                    signal: cancellationController.signal,
                });

                if (response.status === 401) {
                    notice =
                        "登录状态已失效，请重新登录。本地已停止接收，未确认服务端取消。";
                } else if (response.status === 404) {
                    notice =
                        "运行不存在或不可访问。本地已停止接收，未确认服务端取消。";
                } else if (response.status !== 204) {
                    notice =
                        "取消服务暂时不可用。本地已停止接收，未确认服务端取消。";
                }
            }
        } catch {
            notice = "取消请求未完成。本地已停止接收，未确认服务端取消。";
        } finally {
            window.clearTimeout(cancellationTimeoutId);

            if (requestVersionRef.current === requestVersion) {
                setCancellationNotice(notice);
            }

            controller.abort();
        }
    }

    async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault();
        const trimmedPrompt = prompt.trim();
        if (
            !trimmedPrompt ||
            isBusy ||
            creating ||
            creationRef.current ||
            historyLoading ||
            historyError ||
            uncertain ||
            (workbench.localMode && !workbench.selection)
        ) {
            return;
        }

        if (!workbench.localMode) {
            sessionIdRef.current = crypto.randomUUID();
            await startRequest(trimmedPrompt);
        } else if (await prepareTask(trimmedPrompt))
            await startRequest(trimmedPrompt);
    }

    async function handleRetry() {
        if (
            !chatState.lastPrompt ||
            isBusy ||
            creating ||
            !sessionIdRef.current
        ) {
            return;
        }

        await startRequest(chatState.lastPrompt, true);
    }

    function handleStop() {
        void requestCancellation("user");
    }

    // 空白态把引导和输入框放在一起；有消息后恢复底部输入布局。
    const emptyConversation =
        !history.length &&
        !chatState.lastPrompt &&
        !historyLoading &&
        !historyError;
    return (
        <>
            <WorkbenchDetails>
                {workbench.localMode && workbench.selection?.task ? (
                    <TaskChangesPanel
                        key={`${workbench.selection.workspace.external_id}:${workbench.selection.task.external_id}`}
                        workspaceId={workbench.selection.workspace.external_id}
                        taskId={workbench.selection.task.external_id}
                        refreshKey={`${activeRunId}:${chatState.status}:${chatState.tools.filter(tool => tool.result).length}`}
                    />
                ) : <p className="text-sm text-muted-foreground">选择任务后查看文件改动。</p>}
                <details className="mt-6 border-t border-border pt-4">
                    <summary className="cursor-pointer text-sm text-muted-foreground">运行记录与诊断</summary>
                    <div className="mt-4 space-y-4">
                    <section className="pb-1">
                        <h3 className="text-base font-medium">当前运行</h3>

                        <p
                            role="status"
                            aria-live="polite"
                            className="mt-3 text-sm text-muted-foreground"
                        >
                            {statusLabel(chatState.status)}
                        </p>

                        {activeRunId && (
                            <p className="mt-2 break-all text-xs text-muted-foreground">
                                运行编号：{activeRunId}
                            </p>
                        )}
                    </section>

                    {chatState.tools.length > 0 && (
                        <section className="rounded-xl border border-border p-4">
                            <h3 className="text-sm font-medium">工具执行</h3>

                            <ul className="mt-3 space-y-3">
                                {chatState.tools.map((tool) => {
                                    // 保留原有耗时语义：未知不展示，0 ms 正常展示。
                                    const durationLabel = formatToolDuration(
                                        tool.durationMs,
                                    );

                                    return (
                                        <li
                                            key={tool.toolCallId}
                                            className="min-w-0 rounded-lg bg-muted/60 p-3 text-sm"
                                        >
                                            <div className="flex items-start justify-between gap-2">
                                                <code className="min-w-0 break-all font-medium">
                                                    {tool.toolName}
                                                </code>

                                                <span className="shrink-0 text-xs text-muted-foreground">
                                                    {tool.status === "failed"
                                                        ? "失败"
                                                        : tool.status === "succeeded"
                                                          ? (
                                                              tool.toolName === "run_command"
                                                              || tool.toolName === "preview_file_edit"
                                                              || tool.toolName === "preview_file_patch"
                                                                  ? "调用完成"
                                                                  : "成功"
                                                          )
                                                          : (
                                                              chatState.status === "done"
                                                              || chatState.status === "error"
                                                              || chatState.status === "aborted"
                                                                  ? "结果未确认"
                                                                  : "运行中"
                                                          )}
                                                </span>
                                            </div>

                                            <p className="mt-2 break-all text-xs text-muted-foreground">
                                                参数：{tool.arguments}
                                            </p>

                                            {durationLabel !== null && (
                                                <p className="mt-2 text-xs text-muted-foreground">
                                                    耗时：{durationLabel}
                                                </p>
                                            )}

                                            {tool.result && (
                                                <ToolResult
                                                    toolName={tool.toolName}
                                                    result={tool.result}
                                                    taskScope={
                                                        workbench.localMode
                                                        && workbench.selection?.task
                                                            ? {
                                                                workspaceId:
                                                                    workbench.selection.workspace.external_id,
                                                                taskId:
                                                                    workbench.selection.task.external_id,
                                                            }
                                                            : undefined
                                                    }
                                                />
                                            )}

                                            {tool.errorMessage && (
                                                <p className="mt-2 break-all text-destructive">
                                                    错误：{tool.errorMessage}
                                                </p>
                                            )}
                                        </li>
                                    );
                                })}
                            </ul>
                        </section>
                    )}


                    {workbench.localMode &&
                        workbench.rightOpen &&
                        workbench.selection?.task && (
                            <>
                                <TaskSampleStatusPanel
                                    workspaceId={
                                        workbench.selection.workspace.external_id
                                    }
                                    taskId={
                                        workbench.selection.task.external_id
                                    }
                                />
                                <TaskSampleCleanupPreflightPanel
                                    workspaceId={
                                        workbench.selection.workspace.external_id
                                    }
                                    taskId={
                                        workbench.selection.task.external_id
                                    }
                                />
                                <ConversationExecutionPanel
                                    sessionId={
                                        workbench.selection.task.conversation_id
                                    }
                                />
                                <TaskRunPanel
                                    key={
                                        `${workbench.selection.workspace.external_id}:`
                                        + workbench.selection.task.external_id
                                    }
                                    workspaceId={
                                        workbench.selection.workspace.external_id
                                    }
                                    taskId={
                                        workbench.selection.task.external_id
                                    }
                                />
                            </>
                        )}
                    </div>
                </details>
            </WorkbenchDetails>
            <div
                className={`flex min-h-0 flex-1 flex-col ${emptyConversation ? "justify-center overflow-y-auto pb-[8vh]" : ""}`}
            >
                {/* 只有对话内容滚动；底部输入框不随长回复离开视口。 */}
                <section
                    ref={conversationRef}
                    aria-label="对话内容"
                    tabIndex={0}
                    className={`${emptyConversation ? "shrink-0 px-6 pt-8 pb-6" : "min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none] px-6 py-6"} outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring`}
                >
                    <div className="mx-auto w-full max-w-[920px] space-y-6">
                        {historyLoading && (
                            <LoadingPlaceholder label="正在读取对话" />
                        )}
                        {historyError && (
                            <div role="alert" className="text-sm">
                                历史读取失败。
                                <Button
                                    variant="ghost"
                                    onClick={() => {
                                        setHistoryError(false);
                                        setHistoryLoading(true);
                                        setHistoryRetry((n) => n + 1);
                                    }}
                                >
                                    重新读取
                                </Button>
                            </div>
                        )}
                        {history.map((message, index) => (
                            <div
                                key={index}
                                className={
                                    message.role === "user"
                                        ? "flex justify-end"
                                        : ""
                                }
                            >
                                {message.role === "user" ? (
                                    <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-[#edf3ff] px-4 py-2.5 text-sm leading-6 dark:bg-muted">
                                        {message.content}
                                    </p>
                                ) : (
                                    <MarkdownMessage content={message.content} />
                                )}
                            </div>
                        ))}
                        <div ref={currentTurnRef} className="space-y-6">
                            {chatState.lastPrompt ? (
                                <div className="flex justify-end">
                                    <div className="max-w-[85%] rounded-2xl bg-[#edf3ff] px-4 py-2.5 text-sm leading-6 dark:bg-muted">
                                        <p className="whitespace-pre-wrap break-words">
                                            {chatState.lastPrompt}
                                        </p>
                                    </div>
                                </div>
                            ) : !history.length &&
                              !historyLoading &&
                              !historyError ? (
                                <div className="space-y-3">
                                    <h2 className="text-[28px] font-semibold tracking-tight">
                                        今天想完成什么？
                                    </h2>

                                    <p className="mt-3 text-sm text-muted-foreground">
                                        {workbench.selection
                                            ? `一起在 ${workbench.selection.workspace.name} 中完成下一件事。`
                                            : "先在左侧添加项目，然后开始对话。"}
                                    </p>
                                </div>
                            ) : null}

                            {creationError && (
                                <p
                                    role={uncertain ? "alert" : "status"}
                                    className={uncertain ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
                                >
                                    {creationError}
                                </p>
                            )}
                            {uncertain && (
                                <div className="space-y-3 rounded-lg border p-4">
                                    <p className="text-sm text-muted-foreground">
                                        切换项目后可回到这份草稿重试。刷新页面会清除未发送内容和重试信息；刷新前请先确认结果。
                                    </p>
                                    <div className="flex flex-wrap gap-3">
                                        {!creationBlocked && (
                                            <Button type="button" variant="outline" disabled={creating} onClick={retryCreation}>
                                                重试创建
                                            </Button>
                                        )}
                                        <Button type="button" variant="outline" disabled={creating} onClick={startAnotherTask}>
                                            放弃本次重试，开始另一任务
                                        </Button>
                                    </div>
                                    <p className="text-sm text-muted-foreground">
                                        开始另一任务不会删除可能已创建的任务，可从左侧列表查看。
                                    </p>
                                </div>
                            )}
                            {creating && (
                                <p
                                    role="status"
                                    className="text-sm text-muted-foreground"
                                >
                                    正在创建任务…
                                </p>
                            )}
                            {chatState.lastPrompt && (
                                <section aria-label="AI 回复">
                                    <h2 className="sr-only">
                                        Agent
                                    </h2>

                                    {/* 已收到的内容与错误分别展示，失败不遮盖已有回复。 */}
                                    {chatState.reply && (
                                        <MarkdownMessage content={chatState.reply} />
                                    )}

                                    {isBusy && !chatState.reply && (
                                        <p className="text-sm text-muted-foreground">
                                            正在处理…
                                        </p>
                                    )}
                                </section>
                            )}

                            {chatState.errorMessage && (
                                <p
                                    role="alert"
                                    className="break-words text-sm text-destructive"
                                >
                                    {chatState.errorMessage}
                                </p>
                            )}

                            {cancellationNotice && (
                                <p
                                    role="alert"
                                    className="break-words text-sm text-destructive"
                                >
                                    {cancellationNotice}
                                </p>
                            )}
                        </div>
                    </div>
                </section>

                <div
                    className={`shrink-0 px-6 ${emptyConversation ? "pb-8" : "pb-3 pt-3"}`}
                >
                    <form
                        onSubmit={handleSubmit}
                        className="mx-auto w-full max-w-[960px] rounded-[22px] border border-border/60 bg-card p-2 shadow-[0_4px_24px_rgba(0,0,0,0.04)] transition-shadow focus-within:border-foreground/20 focus-within:shadow-[0_4px_24px_rgba(0,0,0,0.07)]"
                    >
                        <Label htmlFor="prompt" className="sr-only">
                            你的问题
                        </Label>

                        <Textarea
                            id="prompt"
                            autoFocus
                            onKeyDown={(event) => {
                                // 中文输入法确认候选词不能触发发送；Shift+Enter 保留换行。
                                if (
                                    event.key === "Enter" &&
                                    !event.shiftKey &&
                                    !event.nativeEvent.isComposing &&
                                    event.keyCode !== 229
                                ) {
                                    event.preventDefault();
                                    event.currentTarget.form?.requestSubmit();
                                }
                            }}
                            value={prompt}
                            disabled={
                                isBusy ||
                                creating ||
                                historyLoading ||
                                historyError ||
                                uncertain ||
                                (workbench.localMode && !workbench.selection)
                            }
                            onChange={(event) => setPrompt(event.target.value)}
                            placeholder="描述问题或任务…"
                            className="min-h-12 max-h-48 resize-y border-0 bg-transparent px-2 py-2 text-sm leading-6 shadow-none focus-visible:ring-0"
                        />

                        <div className="mt-1 flex flex-wrap items-center gap-2">
                            <span
                                title={workbench.selection?.workspace.name}
                                className="mr-auto max-w-[50%] truncate rounded-md px-2 py-1 text-xs text-muted-foreground"
                            >
                                {workbench.selection?.workspace.name ?? "对话"}
                            </span>
                            <span className="hidden text-xs text-muted-foreground/70 sm:inline">
                                Enter 发送 · Shift+Enter 换行
                            </span>
                            {isBusy && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={handleStop}
                                >
                                    停止生成
                                </Button>
                            )}

                            {(chatState.status === "error" ||
                                chatState.status === "aborted") && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={handleRetry}
                                >
                                    {chatState.status === "aborted"
                                        ? "重新生成"
                                        : "重试"}
                                </Button>
                            )}

                            <Button
                                type="submit"
                                disabled={
                                    !prompt.trim() ||
                                    isBusy ||
                                    creating ||
                                    historyLoading ||
                                    historyError ||
                                    uncertain ||
                                    (workbench.localMode &&
                                        !workbench.selection)
                                }
                                size="icon-sm"
                                title={
                                    creating
                                        ? "正在创建…"
                                        : isBusy
                                          ? "生成中…"
                                          : "发送"
                                }
                                aria-label={
                                    creating
                                        ? "正在创建…"
                                        : isBusy
                                          ? "生成中…"
                                          : "发送"
                                }
                                className="rounded-full bg-[#527fe8] text-white hover:bg-[#416ed8] disabled:bg-[#b5c9fa] disabled:opacity-100 dark:disabled:bg-muted"
                            >
                                <WorkbenchIcon name="send" />
                            </Button>
                        </div>
                    </form>
                    {restoredMetrics.failed && <button type="button" onClick={restoredMetrics.retry} className="mx-auto mt-1 block text-xs text-muted-foreground underline">指标读取失败，点击重试</button>}
                    {(
                        <RunMetricsFooter
                            summary={chatState.status === "done" || chatState.status === "error" ? chatState.runSummary : chatState.status === "idle" ? restoredSummary?.summary ?? null : null}
                            failed={chatState.status === "error" || (chatState.status === "idle" && restoredSummary?.status === "error")}
                            running={chatState.status === "thinking" || chatState.status === "streaming"}
                        />
                    )}
                </div>
            </div>
        </>
    );
}
