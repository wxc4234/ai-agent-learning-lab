"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { readStagedData, type StagedData } from "../staged-data";

const labels = { added: "新增", deleted: "删除", modified: "修改", unmerged: "冲突" };
export default function StagedPanel(props: { workspaceId: string; taskId: string }) {
    return <StagedQuery key={`${props.workspaceId}:${props.taskId}`} {...props} />;
}
function StagedQuery({ workspaceId, taskId }: { workspaceId: string; taskId: string }) {
    const [data, setData] = useState<StagedData | null>(null);
    const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
    const [message, setMessage] = useState("");
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => { active.current?.abort(); active.current = null; }, []);
    async function load() {
        if (active.current) return;
        const controller = new AbortController(); active.current = controller;
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]);
        setPhase("loading"); setData(null); setMessage("");
        const base = `/api/workspaces/${workspaceId}/tasks/${taskId}/git`;
        try {
            const binding = await fetch(`${base}/binding`, { signal, cache: "no-store" });
            if (!binding.ok) throw new Error("绑定状态读取失败");
            const meta = await binding.json();
            if (meta.workspace_id !== workspaceId || meta.task_id !== taskId || !Number.isSafeInteger(meta.binding_revision)
                || meta.binding_revision < 1 || typeof meta.bound !== "boolean") throw new Error("绑定响应无效");
            if (!meta.bound) throw new Error("当前项目尚未绑定目录");
            const response = await fetch(`${base}/staged?binding_revision=${meta.binding_revision}`, { signal, cache: "no-store" });
            if (!response.ok) {
                await response.body?.cancel();
                throw new Error(response.status === 409 ? "项目已变化，请重新查询" : response.status === 422
                    ? "项目格式或大小暂不支持，未取得暂存差异" : "暂存差异读取失败，当前结果未知");
            }
            const result = readStagedData(await response.json(), workspaceId, taskId, meta.binding_revision);
            if (!result) throw new Error("暂存差异响应无效");
            signal.throwIfAborted();
            if (active.current === controller) { setData(result); setPhase("ready"); }
        } catch (error) {
            if (active.current === controller) {
                setPhase("error"); setMessage(signal.aborted ? "查询已取消或超时，当前结果未知" : error instanceof Error ? error.message : "读取失败");
            }
        } finally { if (active.current === controller) active.current = null; }
    }
    return <section aria-label="Git暂存差异" className="space-y-3 border-b pb-4">
        <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">Git 暂存差异</h3>
            <Button variant="outline" size="sm" disabled={phase === "loading"} onClick={() => void load()}>查询暂存差异</Button></div>
        <p className="text-xs text-muted-foreground">对比 HEAD 与暂存区；不包含未暂存改动，不代表项目干净。</p>
        {phase === "loading" && <div role="status">正在读取… <Button variant="ghost" size="sm" onClick={() => active.current?.abort()}>取消查询</Button></div>}
        {phase === "error" && <p role="alert" className="text-sm text-destructive">{message}</p>}
        {data?.status === "comparison_unavailable" && <p role="status" className="text-sm">HEAD 或暂存区信息不完整，无法比较。</p>}
        {data?.changes?.length === 0 && <p role="status" className="text-sm">本次未发现暂存差异</p>}
        {!!data?.changes?.length && <div className="max-h-80 space-y-2 overflow-auto">{data.changes.map(item => <div key={item.path} className="break-all text-sm">
            <span className="mr-2 text-muted-foreground">{labels[item.status]}</span><code>{item.path}</code>
            {item.status === "unmerged" && <span className="ml-2 text-xs">stage {item.index.map(v => v.stage).join(" / ")}</span>}
        </div>)}</div>}
    </section>;
}
