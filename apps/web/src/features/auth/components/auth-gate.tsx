"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type GateStatus = "checking" | "ready" | "redirecting" | "error";

type AuthGateProps = {
    children: ReactNode;
    localMode?: boolean;
    returnTo?: "/" | "/workspaces" | "/workspaces/new";
};

function isUser(value: unknown): boolean {
    return (
        typeof value === "object" &&
        value !== null &&
        "external_id" in value &&
        "username" in value &&
        typeof value.external_id === "string" &&
        typeof value.username === "string"
    );
}

export default function AuthGate({ children, localMode = false, returnTo = "/" }: AuthGateProps) {
    const router = useRouter();
    const [status, setStatus] = useState<GateStatus>("checking");
    const activeRequest = useRef<AbortController | null>(null);

    const checkSession = useCallback(async () => {
        if (activeRequest.current !== null) {
            return;
        }

        const controller = new AbortController();
        activeRequest.current = controller;
        setStatus("checking");

        const isCurrent = () =>
            activeRequest.current === controller &&
            !controller.signal.aborted;

        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(15_000),
        ]);

        try {
            const response = await fetch("/api/auth/me", {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                signal,
            });

            if (!isCurrent()) {
                return;
            }

            if (response.status === 401) {
                setStatus("redirecting");
                router.replace(`/login?next=${encodeURIComponent(returnTo)}`);
                return;
            }

            if (response.status !== 200) {
                throw new Error("Session check failed");
            }

            const payload: unknown = await response.json();

            if (!isCurrent()) {
                return;
            }

            if (!isUser(payload)) {
                throw new Error("Invalid user response");
            }

            setStatus("ready");
        } catch {
            if (isCurrent()) {
                setStatus("error");
            }
        } finally {
            if (activeRequest.current === controller) {
                activeRequest.current = null;
            }
        }
    }, [router, returnTo]);

    useEffect(() => {
        if (localMode) return;
        let disposed = false;

        queueMicrotask(() => {
            if (!disposed) {
                void checkSession();
            }
        });

        return () => {
            disposed = true;

            const controller = activeRequest.current;
            activeRequest.current = null;
            controller?.abort();
        };
    }, [checkSession, localMode]);

    if (localMode || status === "ready") {
        return (
            <>
                <nav
                    aria-label="账号导航"
                    className="border-b bg-card px-4 py-3"
                >
                    <div className="mx-auto flex max-w-5xl items-center justify-between">
                        <div className="flex items-center gap-3">
                            <Link href="/">聊天首页</Link>
                            <Link href="/workspaces">工作空间</Link>
                        </div>
                        {localMode ? (
                            <span className="text-sm text-muted-foreground">本地工作台</span>
                        ) : (
                            <Button asChild variant="ghost" size="sm">
                                <Link href="/login">账号与退出</Link>
                            </Button>
                        )}
                    </div>
                </nav>
                {children}
            </>
        );
    }

    return (
        <main className="flex min-h-screen items-center justify-center bg-background px-4">
            <Card
                role="region"
                aria-labelledby="auth-gate-title"
                aria-busy={status !== "error"}
                className="w-full max-w-md gap-4 p-6"
            >
                <h1
                    id="auth-gate-title"
                    className="text-xl font-semibold"
                >
                    {status === "error"
                        ? "暂时无法确认登录状态"
                        : "正在确认登录状态"}
                </h1>

                <p role="status" className="text-sm text-muted-foreground">
                    {status === "error"
                        ? "请检查网络连接后重试。"
                        : status === "redirecting"
                            ? "正在前往登录页…"
                            : "请稍候…"}
                </p>

                {status === "error" && (
                    <Button
                        type="button"
                        variant="outline"
                        onClick={() => void checkSession()}
                    >
                        重新查询登录状态
                    </Button>
                )}
            </Card>
        </main>
    );
}