"use client";

// 内部受控实验组件，产品工作台不导入或挂载；检索应通过后续Agent内部链路使用。

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isCodeContextIdentifier } from "../code-query-context-data";
import type { CodeBatchSummaries } from "../code-batch-summaries-data";
import { loadCodeBatchSummaries, CODE_BATCH_BROWSER_TIMEOUT_MS } from "../code-batch-summaries-request";
import CodeQueryContextPreview from "./code-query-context-preview";

type Scope = { workspaceId: string; taskId: string };
type QueryState =
    | { phase: "idle" | "cancelled" | "loading" | "error" }
    | { phase: "ready"; snapshot: CodeBatchSummaries; selectedBatchId: string | null };

const reasons: Record<string, string> = {
    directory_entries: "单目录条目上限", directory_budget: "目录数量上限",
    depth_budget: "扫描深度上限", file_budget: "文件数量上限",
    unsupported_path: "部分路径不支持", chunk_budget: "分块数量上限",
};

export default function CodeBatchSummariesPanel(props: Scope) {
    // 资源变化重建整个折叠入口；新Task不会短暂显示旧列表或选择。
    return <BatchDisclosure key={`${props.workspaceId}:${props.taskId}`} {...props} />;
}

function BatchDisclosure(props: Scope) {
    const [open, setOpen] = useState(false);
    return (
        <details className="space-y-3 border-b border-border/60 pb-5"
            onToggle={event => setOpen(event.currentTarget.open)}>
            <summary className="cursor-pointer text-base font-medium">代码参考</summary>
            {/* 折叠时卸载读取实例，终止等待并丢弃选择；重新展开不会自动读取。 */}
            {open && <BatchQuery {...props} />}
        </details>
    );
}

function BatchQuery({ workspaceId, taskId }: Scope) {
    const [state, setState] = useState<QueryState>({ phase: "idle" });
    // controller身份同时隔离取消后的迟到fetch/正文与后续新请求。
    const active = useRef<AbortController | null>(null);
    const available = [workspaceId, taskId].every(isCodeContextIdentifier);
    useEffect(() => () => {
        const controller = active.current;
        active.current = null;
        controller?.abort();
    }, []);

    function cancel() {
        const controller = active.current;
        active.current = null;
        controller?.abort();
        setState({ phase: "cancelled" });
    }

    async function load() {
        // React按钮更新前也拦住连续点击；刷新立即使旧选择失效。
        if (!available || active.current !== null) return;
        const controller = new AbortController();
        active.current = controller;
        setState({ phase: "loading" });
        try {
            const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(CODE_BATCH_BROWSER_TIMEOUT_MS)]);
            const snapshot = await loadCodeBatchSummaries(workspaceId, taskId, signal);
            signal.throwIfAborted();
            if (active.current === controller) setState({ phase: "ready", snapshot, selectedBatchId: null });
        } catch {
            if (active.current === controller && !controller.signal.aborted) setState({ phase: "error" });
        } finally {
            if (active.current === controller) active.current = null;
        }
    }

    function select(batchId: string | null) {
        // 只允许当前成功窗口里的显式选择；旧DOM事件不能复活刷新前的批次。
        setState(previous => previous.phase === "ready"
            && (batchId === null || previous.snapshot.batches.some(batch => batch.batch_id === batchId))
            ? { ...previous, selectedBatchId: batchId } : previous);
    }
    const selected = state.phase === "ready"
        ? state.snapshot.batches.find(batch => batch.batch_id === state.selectedBatchId) : undefined;

    return (
        <section aria-label="代码快照选择" aria-busy={state.phase === "loading"} className="mt-3 space-y-3">
            <p className="text-sm text-muted-foreground">
                选择一份已保存的代码快照，用问题查找相关片段。
            </p>
            <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" disabled={!available || state.phase === "loading"}
                    onClick={() => void load()}>
                    {state.phase === "loading" ? "正在读取快照" : state.phase === "idle" ? "读取代码快照" : "重新读取代码快照"}
                </Button>
                {state.phase === "loading" && <Button type="button" variant="ghost" onClick={cancel}>取消读取</Button>}
                {selected && <Button type="button" variant="ghost" onClick={() => select(null)}>清除选择</Button>}
            </div>
            {!available && <p className="text-sm text-muted-foreground">当前任务信息不完整，无法读取代码快照。</p>}
            {available && state.phase === "idle" && <p className="text-sm text-muted-foreground">尚未读取代码快照。</p>}
            {state.phase === "cancelled" && <p role="status" className="text-sm text-muted-foreground">已取消读取，当前结果未知。</p>}
            {state.phase === "loading" && <p role="status" className="text-sm text-muted-foreground">正在读取代码快照…</p>}
            {state.phase === "error" && <p role="alert" className="text-sm text-destructive">快照读取失败，结果未知，请重新读取。</p>}
            {state.phase === "ready" && <>
                <p role="status" className="text-sm">
                    {state.snapshot.batches.length === 0
                        ? "当前任务还没有可用的代码快照。"
                        : `可选择${state.snapshot.batches.length}份代码快照。`}
                </p>
                {state.snapshot.has_more && <p className="text-sm text-muted-foreground">仅显示最近20份，还有更早的快照。</p>}
                {state.snapshot.batches.length > 0 && <fieldset className="min-w-0 space-y-3">
                    <legend className="mb-2 text-sm font-medium">选择代码快照</legend>
                    {state.snapshot.batches.map((batch, index) => <label key={batch.batch_id}
                        className="flex min-w-0 cursor-pointer items-start gap-3 rounded-xl border border-border p-3 text-sm focus-within:ring-2 focus-within:ring-ring">
                        <input type="radio" name={`code-batch:${workspaceId}:${taskId}`} value={batch.batch_id}
                            checked={state.selectedBatchId === batch.batch_id} onChange={() => select(batch.batch_id)}
                            aria-label={`代码快照 ${index + 1}`} className="mt-1 shrink-0 accent-primary" />
                        <span className="min-w-0 flex-1 space-y-1 break-words">
                            <span className="block font-medium">代码快照 {index + 1}</span>
                            <span className="block text-muted-foreground">{batch.created_at.slice(0, 10)} · {batch.chunk_count}个片段{batch.truncated ? " · 部分代码未包含" : ""}</span>
                        </span>
                    </label>)}
                </fieldset>}
                {state.snapshot.batches.length > 0 && <p role="status" aria-live="polite" className="break-all text-sm">
                    {selected ? `已选择快照 ${state.snapshot.batches.indexOf(selected) + 1}。` : "尚未选择代码快照。"}
                </p>}
                {selected && <CodeQueryContextPreview workspaceId={workspaceId} taskId={taskId} batch={selected} />}
                {state.snapshot.batches.length > 0 && <details className="text-xs text-muted-foreground">
                    <summary className="cursor-pointer">快照技术信息</summary>
                    <div className="mt-2 space-y-3 break-all">
                        {state.snapshot.batches.map((batch, index) => <div key={batch.batch_id} className="space-y-1">
                            <p className="font-medium">快照 {index + 1} · 批次 {batch.batch_id}</p>
                            <p>请求模型：{batch.requested_model}</p><p>报告版本：{batch.response_model}</p>
                            <p>模型空间：{batch.space_id} · {batch.dimensions}维</p>
                            <p>{batch.truncated ? `覆盖不完整：${batch.incomplete_reasons.map(reason => reasons[reason]).join("、")}` : "生成时未报告覆盖截断"}</p>
                            <p>创建时间：<time dateTime={batch.created_at}>{batch.created_at}</time></p>
                        </div>)}
                        <p>快照不证明当前文件版本、权限或模型匹配。重新读取、关闭或切换任务会清除选择。</p>
                    </div>
                </details>}
            </>}
        </section>
    );
}
