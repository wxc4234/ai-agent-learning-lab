"use client";

import { Dialog } from "radix-ui";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { readSettings, type ModelSettings } from "./data";

export default function ModelSettingsDialog() {
    return <Dialog.Root>
        <Dialog.Trigger asChild><Button variant="ghost" className="mt-6 w-full justify-start">模型设置</Button></Dialog.Trigger>
        <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
            <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[min(600px,calc(100vw-48px))] -translate-x-1/2 -translate-y-1/2 flex flex-col overflow-hidden rounded-2xl border bg-background p-6 shadow-xl">
                <div className="flex items-center justify-between">
                    <Dialog.Title className="text-xl font-semibold">模型设置</Dialog.Title>
                    <Dialog.Close asChild><Button variant="ghost" aria-label="关闭模型设置">关闭</Button></Dialog.Close>
                </div>
                <Dialog.Description className="mt-2 text-sm text-muted-foreground">
                    配置兼容 OpenAI 接口的服务。密钥只保存于本机后端，不会回显；保存后从下一次请求生效。
                </Dialog.Description>
                <div className="min-h-0 overflow-y-auto pr-1"><Settings /></div>
            </Dialog.Content>
        </Dialog.Portal>
    </Dialog.Root>;
}

function Settings() {
    const [data, setData] = useState<ModelSettings | null>(null);
    const [error, setError] = useState(false);
    const [kind, setKind] = useState<"chat" | "embedding">("chat");
    useEffect(() => {
        const controller = new AbortController();
        void fetch("/api/model-settings", { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) })
            .then(async res => { const parsed = readSettings(await res.json()); if (!res.ok || !parsed) throw new Error(); if (!controller.signal.aborted) setData(parsed); })
            .catch(() => { if (!controller.signal.aborted) setError(true); });
        return () => controller.abort();
    }, []);
    return <>
        <div className="my-5 flex gap-2" aria-label="配置用途">
            <Button variant={kind === "chat" ? "default" : "outline"} onClick={() => setKind("chat")}>聊天模型</Button>
            <Button variant={kind === "embedding" ? "default" : "outline"} onClick={() => setKind("embedding")}>代码检索模型</Button>
        </div>
        {error ? <p role="alert">读取失败，请关闭后重试。</p> : !data ? <p role="status">正在读取配置…</p>
            : <ModelForm key={kind} kind={kind} data={data} onSaved={setData} />}
    </>;
}

function ModelForm({ kind, data, onSaved }: { kind: "chat" | "embedding"; data: ModelSettings; onSaved: (v: ModelSettings) => void }) {
    const source = data[kind];
    const [enabled, setEnabled] = useState(source.enabled);
    const [url, setUrl] = useState(source.base_url);
    const [model, setModel] = useState(source.model);
    const [key, setKey] = useState("");
    const [clear, setClear] = useState(false);
    const [dimension, setDimension] = useState(source.dimensions?.toString() ?? "");
    const [requestDimensions, setRequestDimensions] = useState(source.request_dimensions);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => active.current?.abort(), []);
    const inputStyle = "mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm";
    const wrongEmbedding = kind === "embedding" && /^https?:\/\/api\.deepseek\.com(?:\/|$)/i.test(url);
    const config = () => ({ enabled, base_url: url.trim(), model: model.trim(), api_key: key,
        dimensions: kind === "embedding" && dimension ? Number(dimension) : null,
        request_dimensions: kind === "embedding" && requestDimensions });
    async function perform(detect: boolean) {
        const controller = new AbortController();
        active.current = controller;
        setBusy(true); setMessage("");
        try {
            const response = await fetch("/api/model-settings" + (detect ? "/detect-dimensions" : ""), {
                method: detect ? "POST" : "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(detect ? config() : { channel: kind, revision: data.revision, config: config(), clear_key: clear }),
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
            });
            const raw = await response.json();
            controller.signal.throwIfAborted();
            if (!response.ok) throw new Error(response.status === 409 ? "配置已变化，请关闭后重新打开。" : detect ? "检测失败，请确认服务支持Embedding，并检查地址、模型和密钥。" : "未确认保存，请检查配置；重新打开可核对当前值。");
            if (detect) {
                if (!Number.isInteger(raw.dimensions) || raw.dimensions < 1 || raw.dimensions > 4096) throw new Error("检测结果无效");
                setDimension(String(raw.dimensions));
                setRequestDimensions(false);
                setMessage("已检测到模型默认输出维度；点击保存后生效。");
            } else {
                const parsed = readSettings(raw);
                if (!parsed) throw new Error("未确认保存，请重新打开核对。");
                onSaved(parsed); setKey(""); setClear(false); setMessage("已保存，下一次请求生效。");
            }
        } catch (error) {
            if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "操作失败，请重试。");
        } finally { if (!controller.signal.aborted) setBusy(false); }
    }
    return <form onSubmit={event => { event.preventDefault(); void perform(false); }} className="space-y-4">
        <p className="text-sm text-muted-foreground">{kind === "chat" ? "用于对话、工具调用和任务标题，不需要向量维度。" : "可选。用于把代码和查询转成向量，与聊天模型独立。未配置时仍可正常聊天。"}</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} disabled={busy} />启用{kind === "chat" ? "聊天模型" : "代码检索模型"}</label>
        <fieldset disabled={busy} className="space-y-4">
            <label className="block text-sm">服务地址<input className={inputStyle} type="url" required={enabled} value={url} onChange={e => setUrl(e.target.value)} placeholder="https://服务地址/v1" autoComplete="off" /></label>
            <label className="block text-sm">模型名称<input className={inputStyle} required={enabled} maxLength={256} value={model} onChange={e => setModel(e.target.value)} placeholder="填写服务商提供的模型 ID" autoComplete="off" /></label>
            <label className="block text-sm">API Key<input className={inputStyle} type="password" value={key} onChange={e => { setKey(e.target.value); setClear(false); }} maxLength={4096} autoComplete="new-password" placeholder={source.key_configured ? "已设置，留空保留；更换地址时请填写新密钥" : "填写此服务的 API Key"} /></label>
            {source.key_configured && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={clear} onChange={e => { setClear(e.target.checked); setKey(""); }} />清除已保存密钥（需同时停用）</label>}
            {kind === "embedding" && <>
                {wrongEmbedding && <p role="alert" className="text-sm text-destructive">此 DeepSeek 地址用于聊天，请选择支持 Embedding 的服务。</p>}
                <label className="block text-sm">向量维度<input className={inputStyle} type="number" min={1} max={4096} required={enabled} value={dimension} onChange={e => setDimension(e.target.value)} placeholder="可通过下方按钮检测，无需猜测" /></label>
                <p className="text-xs text-muted-foreground">维度是模型输出的数字个数，不是上下文长度。检测会发送一小段固定测试文字，可能产生少量费用，不发送项目内容。</p>
                <Button type="button" variant="outline" disabled={wrongEmbedding || !url || !model || clear} onClick={() => void perform(true)}>检测维度</Button>
                <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={requestDimensions} onChange={e => setRequestDimensions(e.target.checked)} />请求指定维度（仅在模型文档明确支持时开启）</label>
            </>}
        </fieldset>
        <div className="flex items-center gap-3"><Button type="submit" disabled={busy || enabled && wrongEmbedding}>{busy ? "处理中…" : "保存配置"}</Button><span className="text-xs text-muted-foreground">保存不会发起模型请求</span></div>
        {message && <p role="status" className="text-sm">{message}</p>}
    </form>;
}
