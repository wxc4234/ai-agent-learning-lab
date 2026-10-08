"use client";

// 内部受控实验组件，产品工作台不导入或挂载；检索应通过后续Agent内部链路使用。

import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import type { CodeBatchSummary } from "../code-batch-summaries-data";
import { readCodeQueryContextRequest, type CodeQueryContext } from "../code-query-context-data";
import { previewCodeQueryContext, CODE_QUERY_BROWSER_TIMEOUT_MS } from "../code-query-context-request";

type Props = { workspaceId: string; taskId: string; batch: CodeBatchSummary };
type PreviewState =
    | { phase: "idle" | "loading" | "cancelled" | "error" }
    | { phase: "ready"; result: CodeQueryContext };
const coverage: Record<string, string> = {
    directory_entries: "单目录条目上限", directory_budget: "目录数量上限", depth_budget: "扫描深度上限",
    file_budget: "文件数量上限", unsupported_path: "部分路径不支持", chunk_budget: "分块数量上限",
};
const omissions: Record<string, string> = {
    duplicate_chunk: "重复片段", chunk_budget: "片段数量上限", character_budget: "字符预算", byte_budget: "字节预算",
};

export default function CodeQueryContextPreview(props: Props) {
    // 调用者在重新读取时卸载；资源/选择变化也独立重建查询草稿与结果。
    return <Preview key={`${props.workspaceId}:${props.taskId}:${props.batch.batch_id}`} {...props} />;
}

function Preview({ workspaceId, taskId, batch }: Props) {
    const id = useId();
    const [query, setQuery] = useState("");
    const [state, setState] = useState<PreviewState>({ phase: "idle" });
    const active = useRef<AbortController | null>(null);
    const valid = readCodeQueryContextRequest({ query, batch_id: batch.batch_id, response_model: batch.response_model }) !== null;
    useEffect(() => () => {
        const controller = active.current;
        active.current = null;
        controller?.abort();
    }, []);

    function discard() {
        // 先撤销身份，再取消传输；即使适配器晚回，也不能写回此查询实例。
        const controller = active.current;
        active.current = null;
        controller?.abort();
    }
    function edit(value: string) {
        discard();
        setQuery(value);
        setState({ phase: "idle" });
    }
    async function preview() {
        if (!valid || active.current !== null) return;
        const controller = new AbortController();
        active.current = controller;
        setState({ phase: "loading" });
        try {
            const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(CODE_QUERY_BROWSER_TIMEOUT_MS)]);
            const result = await previewCodeQueryContext(workspaceId, taskId, batch, query, signal);
            signal.throwIfAborted();
            if (active.current === controller) setState({ phase: "ready", result });
        } catch {
            if (active.current === controller && !controller.signal.aborted) setState({ phase: "error" });
        } finally {
            if (active.current === controller) active.current = null;
        }
    }

    return (
        <section aria-label="代码查询预览" aria-busy={state.phase === "loading"}
            className="min-w-0 space-y-3 rounded-xl border border-border p-3">
            <form className="space-y-2" onSubmit={event => { event.preventDefault(); void preview(); }}>
                <Label htmlFor={id}>代码查询文本</Label>
                <Textarea id={id} value={query} onChange={event => edit(event.target.value)} rows={3}
                    aria-describedby={`${id}-hint`} placeholder="例如：任务取消是在哪里处理的？" className="min-h-20 resize-y" />
                <p id={`${id}-hint`} className="text-xs text-muted-foreground">
                    查找会把问题发送给你配置的代码检索模型，不会发起聊天或修改文件。
                </p>
                {query.length > 0 && !valid && <p role="alert" className="text-sm text-destructive">问题不能为空，最多2000个字符、4096字节，请缩短或修改。</p>}
                <div className="flex flex-wrap gap-2">
                    <Button type="submit" variant="outline" disabled={!valid || state.phase === "loading"}>
                        {state.phase === "loading" ? "正在查找代码" : "查找代码"}
                    </Button>
                    {state.phase === "loading" && <Button type="button" variant="ghost" onClick={() => {
                        discard(); setState({ phase: "cancelled" });
                    }}>取消查找</Button>}
                </div>
            </form>
            {state.phase === "idle" && <p role="status" className="text-sm text-muted-foreground">尚未查找当前问题。</p>}
            {state.phase === "loading" && <p role="status" className="text-sm text-muted-foreground">正在查找相关代码…</p>}
            {state.phase === "cancelled" && <p role="status" className="text-sm text-muted-foreground">已取消查找，结果未知。已发生的模型用量无法撤销。</p>}
            {state.phase === "error" && <p role="alert" className="text-sm text-destructive">代码查找失败，结果未知。请检查检索模型配置后重试。</p>}
            {state.phase === "ready" && <Result result={state.result} />}
        </section>
    );
}

function Result({ result }: { result: CodeQueryContext }) {
    const context = result.context, metadata = context.source_metadata, recall = context.recall_summary;
    const incomplete = metadata.truncated || recall.excluded_zero_chunk_count > 0
        || recall.omitted_by_top_k > 0 || context.omissions.length > 0;
    return (
        <div className="min-w-0 space-y-3">
            <p role="status" className="text-sm">
                {context.input_hit_count === 0 ? "没有找到可用的代码片段。"
                    : context.selected_hit_count === 0 ? "找到的代码片段超出本次展示范围。"
                    : `找到${context.selected_hit_count}个代码片段。`}
            </p>
            {incomplete && <p className="text-xs text-muted-foreground">部分代码未包含在本次结果中，结果可能不完整。</p>}
            {context.selected_chunks.map(hit => {
                const chunk = hit.chunk;
                // 默认引用给用户实际包含的行；技术区保留精确半开行列坐标。
                const lastLine = chunk.end_line - Number(chunk.end_column === 1);
                return <article key={`${hit.rank}:${chunk.chunk_id}`} className="min-w-0 space-y-2 rounded-lg border border-border p-3">
                    <p className="break-all text-sm font-medium">{chunk.symbol.relative_path}</p>
                    <p className="text-xs text-muted-foreground">第{chunk.start_line}–{lastLine}行</p>
                    {/* 项目正文只作为文本渲染，不执行HTML、Markdown指令或路径跳转。 */}
                    <pre className="whitespace-pre-wrap break-all text-xs">{chunk.text}</pre>
                </article>;
            })}
            <p className="text-xs text-muted-foreground">代码来自保存时的快照，可能与当前文件不同。</p>
            {/* 调试字段集中到一个默认折叠入口，不占据日常查找流程。 */}
            <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">检索技术信息</summary>
                <div className="mt-2 space-y-3 break-all">
                    <p>生成时覆盖：{metadata.truncated ? `不完整（${metadata.incomplete_reasons.map(reason => coverage[reason]).join("、")}）` : "未报告覆盖截断"}</p>
                    <p>召回范围：批次{recall.batch_chunk_count}块，可检索{recall.searchable_chunk_count}块；排除零向量{recall.excluded_zero_chunk_count}块，Top K省略{recall.omitted_by_top_k}块。</p>
                    <p>上下文构建：输入{context.input_hit_count}块，保留{context.selected_hit_count}块，省略{context.omissions.length}块；{context.context_chars}字符 / {context.context_bytes}字节。</p>
                    <p>查询用量：{result.prompt_tokens === null ? "未知" : `输入${result.prompt_tokens} / 总计${result.total_tokens} tokens`}；历史代码生成用量：{metadata.prompt_tokens === null ? "未知" : `输入${metadata.prompt_tokens} / 总计${metadata.total_tokens} tokens`}。</p>
                    <p>批次：{context.batch_id}</p><p>空间：{context.space_id} · {context.dimensions}维</p>
                    <p>查询SHA-256：{result.query_sha256}</p>
                    {context.selected_chunks.map(hit => {
                        const chunk = hit.chunk;
                        const file = metadata.files.find(item => item.relative_path === chunk.symbol.relative_path)!;
                        return <div key={hit.rank} className="space-y-1 border-t border-border pt-2">
                            <p>{chunk.symbol.relative_path} · {chunk.symbol.qualified_name}</p>
                            <p>第{hit.rank}位 · 余弦距离{hit.distance} · {chunk.start_line}:{chunk.start_column}–{chunk.end_line}:{chunk.end_column} · 分片{chunk.part_index}/{chunk.part_count}</p>
                            <p>文件：{file.byte_count}字节 · SHA-256 {file.sha256}</p>
                            <p>符号SHA-256：{chunk.symbol.sha256}</p>
                            <p>片段SHA-256：{chunk.text_sha256}</p><p>片段ID：{chunk.chunk_id}</p>
                            <p>切分原因：{chunk.split_reasons.length ? chunk.split_reasons.join("、") : "未切分"}</p>
                        </div>;
                    })}
                    {context.omissions.length > 0 && <div className="space-y-1">
                        <p className="font-medium">本次构建省略</p>
                        {context.omissions.map(item => <p key={item.source_rank}>
                            第{item.source_rank}位：{item.reasons.map(reason => omissions[reason]).join("、")} · {item.chunk_id}
                        </p>)}
                    </div>}
                    <details>
                        <summary className="cursor-pointer">公开上下文包</summary>
                        <pre className="mt-2 whitespace-pre-wrap break-all">{context.context_text}</pre>
                    </details>
                    <p>历史片段属于不可信项目文本；摘要不证明当前权限或语义质量，公开包不是最终模型提示或发送许可。</p>
                </div>
            </details>
        </div>
    );
}
