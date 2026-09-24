"use client";

import { useEffect, useRef, useState } from "react";
import type { SubmitEvent } from "react";
import Link from "next/link";
import { Dialog } from "radix-ui";
import WorkspaceDirectoryPanel from "@/features/workbench/components/workspace-directory-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";

type Workspace = { external_id: string; name: string; created_at: string };
type State =
    | { status: "idle" | "submitting" | "uncertain" | "unauthorized" }
    | { status: "rejected"; message: string }
    | { status: "success"; workspace: Workspace };

function readWorkspace(value: unknown): Workspace | null {
    if (
        typeof value !== "object" || value === null || Array.isArray(value) ||
        !("external_id" in value) || typeof value.external_id !== "string" ||
        !/^[a-f0-9]{32}$/.test(value.external_id) ||
        !("name" in value) || typeof value.name !== "string" ||
        Array.from(value.name).length < 1 || Array.from(value.name).length > 100 ||
        !("created_at" in value) || typeof value.created_at !== "string" ||
        !Number.isFinite(Date.parse(value.created_at))
    ) return null;
    return { external_id: value.external_id, name: value.name, created_at: value.created_at };
}

export default function CreateWorkspace({ localMode, modal }: {
    localMode: boolean;
    modal?: { open: boolean; onOpenChange: (open: boolean) => void; onCreated: () => void };
}) {
    const [name, setName] = useState("");
    const [state, setState] = useState<State>({ status: "idle" });
    const active = useRef<AbortController | null>(null);
    useEffect(() => () => {
        const controller = active.current;
        active.current = null;
        controller?.abort();
    }, []);

    async function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault();
        // ref 立即占位，避免快速点击在 React 更新状态前发出第二个请求。
        if (active.current || !["idle", "rejected"].includes(state.status)) return;
        const controller = new AbortController();
        active.current = controller;
        setState({ status: "submitting" });
        const current = () => active.current === controller && !controller.signal.aborted;
        try {
            const response = await fetch("/api/workspaces", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
                credentials: "same-origin", cache: "no-store",
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
            });
            if (!current()) return;
            if (response.status === 401 && !localMode) {
                setState({ status: "unauthorized" });
                return;
            }
            if ([400, 403, 415, 422].includes(response.status)) {
                setState({ status: "rejected", message: response.status === 422
                    ? "名称去除首尾空白后须为 1～100 个字符。"
                    : "请求被拒绝，请检查本地服务配置后再试。" });
                return;
            }
            // 提交可能已成功；未知响应与网络故障均不自动重试。
            if (response.status !== 201) {
                setState({ status: "uncertain" });
                return;
            }
            const workspace = readWorkspace(await response.json());
            if (current()) {
                setState(workspace ? { status: "success", workspace } : { status: "uncertain" });
                if (workspace) modal?.onCreated();
            }
        } catch {
            if (current()) setState({ status: "uncertain" });
        } finally {
            if (active.current === controller) active.current = null;
        }
    }

    const canSubmit = state.status === "idle" || state.status === "rejected";
    if (modal) {
        // 组件在侧栏持续挂载，关闭浮窗不丢失未确认的写入状态或草稿。
        const changeOpen = (open: boolean) => {
            if (active.current) return;
            if (open && state.status === "success") {
                setState({ status: "idle" });
                setName("");
            }
            modal.onOpenChange(open);
        };
        return (
            <Dialog.Root open={modal.open} onOpenChange={changeOpen}>
                <Dialog.Trigger asChild><Button variant="ghost" size="icon-xs" title="添加项目" aria-label="添加项目">+</Button></Dialog.Trigger>
                <Dialog.Portal>
                    <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20 backdrop-blur-[2px]" />
                    <Dialog.Content onEscapeKeyDown={event => { if (active.current) event.preventDefault(); }}
                        onPointerDownOutside={event => event.preventDefault()}
                        className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[calc(100%-48px)] max-w-[560px] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl border border-border/60 bg-background p-6 text-sm text-foreground shadow-2xl focus:outline-none">
                        <div className="mb-5 flex items-center justify-between">
                            <Dialog.Title className="text-xl font-semibold">{state.status === "success" ? "项目已创建" : "创建项目"}</Dialog.Title>
                            <Dialog.Close asChild><Button aria-label="关闭创建项目" variant="ghost" size="icon-sm" disabled={state.status === "submitting"}>×</Button></Dialog.Close>
                        </div>
                        <Dialog.Description className="sr-only">创建项目并添加本机源文件夹，当前对话保持不变。</Dialog.Description>
                        {state.status === "success" ? (
                            <div className="space-y-5">
                                <p role="status" className="break-words font-medium">{state.workspace.name}</p>
                                {localMode && <WorkspaceDirectoryPanel workspaceId={state.workspace.external_id} />}
                                <div className="flex justify-end"><Dialog.Close asChild><Button>完成</Button></Dialog.Close></div>
                            </div>
                        ) : (
                            <form onSubmit={submit} className="space-y-5" aria-busy={state.status === "submitting"}>
                                <Label htmlFor="project-name" className="sr-only">项目名称</Label>
                                <Input id="project-name" autoFocus required placeholder="项目名称" value={name}
                                    disabled={!canSubmit} onChange={event => setName(event.target.value)} className="h-11 rounded-xl" />
                                <div className="space-y-2">
                                    <p className="font-medium">源文件夹</p>
                                    <div className="rounded-xl border border-border/70 px-5 py-7 text-center text-muted-foreground">
                                        创建后可在此选择本机文件夹
                                    </div>
                                </div>
                                {state.status === "rejected" && <p role="alert" className="text-destructive">{state.message}</p>}
                                {state.status === "unauthorized" && <p role="alert">登录已失效，请重新登录。</p>}
                                {state.status === "uncertain" && <div role="alert" className="space-y-2">
                                    <p>暂时无法确认创建结果，请先核对侧栏列表，避免重复创建。</p>
                                    <Button type="button" variant="outline" onClick={modal.onCreated}>刷新项目列表</Button>
                                </div>}
                                <div className="flex justify-end gap-3 pt-2">
                                    <Dialog.Close asChild><Button type="button" variant="ghost" disabled={state.status === "submitting"}>取消</Button></Dialog.Close>
                                    <Button type="submit" disabled={!canSubmit || !name.trim()} className="rounded-xl bg-foreground text-background hover:bg-foreground/85">
                                        {state.status === "submitting" ? "正在创建…" : "创建项目"}
                                    </Button>
                                </div>
                            </form>
                        )}
                    </Dialog.Content>
                </Dialog.Portal>
            </Dialog.Root>
        );
    }
    return (
        <main className="min-h-screen bg-background px-6 py-10 text-foreground">
            <div className="mx-auto max-w-5xl">
                <h1 className="text-3xl font-semibold">创建工作空间</h1>
                <p className="mt-3 mb-8 text-muted-foreground">为你的 Agent 项目保存任务和运行记录。</p>
                <Card aria-busy={state.status === "submitting"} className="max-w-2xl p-8">
                    <form onSubmit={submit} className="space-y-6">
                        <div className="space-y-2">
                            <Label htmlFor="workspace-name">工作空间名称</Label>
                            <Input id="workspace-name" required value={name}
                                onChange={event => setName(event.target.value)} disabled={!canSubmit}
                                aria-describedby="name-help" placeholder="例如：代码助手项目" className="h-11" />
                            <p id="name-help" className="text-sm text-muted-foreground">去除首尾空白后为 1～100 个字符。</p>
                        </div>
                        <div className="flex gap-3">
                            <Button type="submit" disabled={!canSubmit} className="h-11">
                                {state.status === "submitting" ? "正在创建…" : "创建工作空间"}
                            </Button>
                            <Button
                                asChild
                                variant="outline"
                                className="h-11"
                            >
                                <Link href="/workspaces">
                                    返回工作空间列表
                                </Link>
                            </Button>
                        </div>
                    </form>
                    {state.status === "rejected" && <p role="alert">{state.message}</p>}
                    {state.status === "unauthorized" && <p role="alert">登录已失效，<Link href="/login?next=%2Fworkspaces%2Fnew" className="underline">前往登录</Link>。</p>}
                    {state.status === "uncertain" && <p role="alert">暂时无法确认创建结果，工作空间可能已经创建。请勿立即重复提交。</p>}
                    {state.status === "success" && (
                        <section role="status" className="rounded-lg bg-muted p-5">
                            <h2 className="font-semibold">工作空间已创建</h2>
                            <p className="mt-3 break-words">{state.workspace.name}</p>
                            <p className="mt-2 text-sm text-muted-foreground">{new Date(state.workspace.created_at).toLocaleString("zh-CN")}</p>
                        </section>
                    )}
                </Card>
            </div>
        </main>
    );
}
