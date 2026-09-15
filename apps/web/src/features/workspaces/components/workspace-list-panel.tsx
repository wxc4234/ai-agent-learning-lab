"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
    readWorkspaceList,
    type WorkspaceListData,
} from "../workspace-list";

const LIST_LIMIT = 20;

type ListState =
    | { status: "loading" }
    | { status: "ready"; data: WorkspaceListData }
    | { status: "error" }
    | { status: "unauthorized" };

export default function WorkspaceListPanel({
    localMode,
}: {
    localMode: boolean;
}) {
    const [state, setState] = useState<ListState>({
        status: "loading",
    });

    const activeRequest = useRef<AbortController | null>(null);

    const load = useCallback(async () => {
        // 同步占位，避免连续点击刷新发出重复请求。
        if (activeRequest.current !== null) {
            return;
        }

        const controller = new AbortController();
        activeRequest.current = controller;
        setState({ status: "loading" });

        const isCurrent = () =>
            activeRequest.current === controller &&
            !controller.signal.aborted;

        try {
            const response = await fetch(
                `/api/workspaces?limit=${LIST_LIMIT}`,
                {
                    method: "GET",
                    credentials: "same-origin",
                    cache: "no-store",
                    signal: AbortSignal.any([
                        controller.signal,
                        AbortSignal.timeout(15_000),
                    ]),
                },
            );

            if (!isCurrent()) {
                return;
            }

            if (response.status === 401 && !localMode) {
                setState({ status: "unauthorized" });
                return;
            }

            if (response.status !== 200) {
                throw new Error("Workspace list request failed");
            }

            const payload: unknown = await response.json();

            if (!isCurrent()) {
                return;
            }

            const data = readWorkspaceList(payload, LIST_LIMIT);

            if (data === null) {
                throw new Error("Invalid workspace list response");
            }

            setState({ status: "ready", data });
        } catch {
            // 离开页面后，旧请求不能继续更新组件。
            if (isCurrent()) {
                setState({ status: "error" });
            }
        } finally {
            if (activeRequest.current === controller) {
                activeRequest.current = null;
            }
        }
    }, [localMode]);

    useEffect(() => {
        let disposed = false;

        queueMicrotask(() => {
            if (!disposed) {
                void load();
            }
        });

        return () => {
            disposed = true;

            const controller = activeRequest.current;
            activeRequest.current = null;
            controller?.abort();
        };
    }, [load]);

    return (
        <main className="min-h-screen bg-background px-6 py-10 text-foreground">
            <div className="mx-auto max-w-5xl">
                <header className="mb-8 flex items-center justify-between gap-6">
                    <div>
                        <h1 className="text-3xl font-semibold">
                            工作空间
                        </h1>
                        <p className="mt-3 text-muted-foreground">
                            查看最近创建的 Agent 项目工作空间。
                        </p>
                    </div>

                    <div className="flex shrink-0 items-center gap-3">
                        <Button
                            type="button"
                            variant="outline"
                            disabled={state.status === "loading"}
                            onClick={() => void load()}
                        >
                            {state.status === "loading"
                                ? "正在加载…"
                                : "刷新列表"}
                        </Button>

                        <Button asChild>
                            <Link href="/workspaces/new">
                                创建工作空间
                            </Link>
                        </Button>
                    </div>
                </header>

                <Card
                    aria-busy={state.status === "loading"}
                    className="gap-0 overflow-hidden p-0"
                >
                    {state.status === "loading" && (
                        <p role="status" className="p-8 text-muted-foreground">
                            正在读取工作空间…
                        </p>
                    )}

                    {state.status === "error" && (
                        <div role="alert" className="space-y-4 p-8">
                            <p>暂时无法读取工作空间，请检查服务后重试。</p>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => void load()}
                            >
                                重新加载
                            </Button>
                        </div>
                    )}

                    {state.status === "unauthorized" && (
                        <div role="alert" className="space-y-4 p-8">
                            <p>登录状态已失效，请重新登录。</p>
                            <Button asChild>
                                <Link href="/login?next=%2Fworkspaces">
                                    前往登录
                                </Link>
                            </Button>
                        </div>
                    )}

                    {state.status === "ready" &&
                        state.data.items.length === 0 && (
                            <div className="p-10 text-center">
                                <h2 className="text-lg font-semibold">
                                    还没有工作空间
                                </h2>
                                <p className="mt-3 text-muted-foreground">
                                    点击右上角“创建工作空间”开始。
                                </p>
                            </div>
                        )}

                    {state.status === "ready" &&
                        state.data.items.length > 0 && (
                            <>
                                <table className="w-full table-fixed text-left">
                                    <caption className="sr-only">
                                        最近创建的工作空间
                                    </caption>
                                    <thead className="border-b bg-muted text-sm">
                                        <tr>
                                            <th scope="col" className="px-6 py-4">
                                                名称
                                            </th>
                                            <th
                                                scope="col"
                                                className="w-64 px-6 py-4"
                                            >
                                                创建时间
                                            </th>
                                        </tr>
                                    </thead>

                                    <tbody>
                                        {state.data.items.map((item) => (
                                            <tr
                                                key={item.external_id}
                                                className="border-b last:border-b-0"
                                            >
                                                <td className="break-all px-6 py-5">
                                                    {item.name}
                                                </td>
                                                <td className="px-6 py-5 text-sm text-muted-foreground">
                                                    {new Date(
                                                        item.created_at,
                                                    ).toLocaleString("zh-CN")}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>

                                {state.data.has_more && (
                                    <p className="border-t bg-muted px-6 py-4 text-sm text-muted-foreground">
                                        当前仅显示最近 {LIST_LIMIT} 个工作空间，
                                        还有更早的记录未显示。
                                    </p>
                                )}
                            </>
                        )}
                </Card>
            </div>
        </main>
    );
}
