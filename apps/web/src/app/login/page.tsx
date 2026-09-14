"use client";

import { useCallback, useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { SubmitEvent } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";

type User = {
    external_id: string;
    username: string;
};

type AuthState =
    | { status: "checking" }
    | { status: "anonymous" }
    | { status: "authenticated"; user: User }
    | { status: "unknown" };

type Operation = "check" | "login" | "logout";

type Credentials = {
    username: string;
    password: string;
};

const FAILURE_MESSAGES: Record<Operation, string> = {
    check: "暂时无法确认登录状态，请重新查询。",
    login: "登录未能确认，请重新查询登录状态后再试。",
    logout: "退出结果尚未确认，请重新查询登录状态。",
};

function readUser(value: unknown): User | null {
    if (
        typeof value !== "object" ||
        value === null ||
        !("external_id" in value) ||
        !("username" in value) ||
        typeof value.external_id !== "string" ||
        typeof value.username !== "string"
    ) {
        return null;
    }

    return {
        external_id: value.external_id,
        username: value.username,
    };
}

export default function LoginPage() {
    const router = useRouter();
    const [auth, setAuth] = useState<AuthState>({
        status: "checking",
    });
    const [pending, setPending] = useState<Operation | null>("check");
    const [message, setMessage] = useState("");
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");

    const activeRequest = useRef<AbortController | null>(null);

    const perform = useCallback(async (
        operation: Operation,
        credentials?: Credentials,
    ) => {
        if (activeRequest.current !== null) {
            return;
        }

        const controller = new AbortController();
        activeRequest.current = controller;

        setPending(operation);
        setMessage("");

        const isCurrent = () =>
            activeRequest.current === controller
            && !controller.signal.aborted;

        const timeoutSignal = AbortSignal.timeout(15_000);
        const signal = AbortSignal.any([
            controller.signal,
            timeoutSignal
        ]);

        try {
            let response: Response;

            if (operation === "login") {
                response = await fetch("/api/auth/login", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(credentials),
                    credentials: "same-origin",
                    cache: "no-store",
                    signal,
                });
            }
            else {
                response = await fetch(
                    operation === "check" ? "/api/auth/me" : "/api/auth/logout",
                    {
                        method: operation === "check" ? "GET" : "POST",
                        credentials: "same-origin",
                        cache: "no-store",
                        signal
                    },
                );
            }

            if (!isCurrent()) {
                return;
            }

            if (operation === "logout") {
                if (response.status !== 204) {
                    throw new Error("Logout was not confirmed");
                }

                setAuth({ status: "anonymous"});
                setPassword("");
                setMessage("已退出登录");

                return;
            }

            if (response.status === 401) {
                setAuth({ status: "anonymous"});

                if (operation === "login") {
                    setPassword("");
                    setMessage("用户名或密码错误，请重新输入");
                }
                return;
            }

            if (operation === "login" && response.status === 422) {
                setAuth({ status: "anonymous" });
                setPassword("");
                setMessage("登录信息不符合要求，请检查用户名和密码。");
                return;
            }

            if (response.status !== 200) {
                throw new Error("Authentication request failed");
            }

            const payload: unknown = await response.json();

            if (!isCurrent()) {
                return;
            }

            const user = readUser(payload);

            if (user === null) {
                throw new Error("Invalid user response");
            }

            setAuth({ status: "authenticated", user });
            setPassword("");

            if (operation === "login") {
                setMessage("登录成功。");
            }
        }
        catch {
            if (!isCurrent()) {
                return;
            }

            setAuth({ status: "unknown" });
            setPassword("");
            setMessage(FAILURE_MESSAGES[operation]);
        }
        finally {
            if (activeRequest.current === controller) {
                activeRequest.current = null;
                setPending(null);
            }
        }
    }, [])

    useEffect(() => {
         let disposed = false;

        queueMicrotask(() => {
            if (!disposed) {
                void perform("check");
            }
        });

        return () => {
            disposed = true;

            const controller = activeRequest.current;
            activeRequest.current = null;
            controller?.abort();
        };
    }, [perform]);

    useEffect(() => {
        if (auth.status !== "authenticated" || pending !== null) {
            return;
        }

        const destinations = new URLSearchParams(
            window.location.search,
        ).getAll("next");

        if (destinations.length === 1 && destinations[0] === "/") {
            router.replace("/");
        }
    }, [auth.status, pending, router]);

    function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault();

        if (pending !== null || auth.status !== "anonymous") {
            return;
        }

        void perform("login", { username, password });
    }

    const busy = pending !== null;

    return (
        <main className="flex min-h-screen items-center justify-center bg-background px-4 py-12 text-foreground">
            <Card
                role="region"
                aria-labelledby="login-title"
                aria-busy={busy}
                className="w-full max-w-md gap-0 p-6 shadow-sm sm:p-8"
            >
                <p className="text-sm text-muted-foreground">
                    AI Agent Learning Lab
                </p>

                <h1
                    id="login-title"
                    className="mt-2 text-2xl font-semibold"
                >
                    账号登录
                </h1>

                {pending === "check" && (
                    <p role="status" className="mt-6 text-muted-foreground">
                        正在查询登录状态…
                    </p>
                )}

                {message && (
                    <p
                        role="status"
                        aria-live="polite"
                        className="mt-6 rounded-lg bg-muted p-3 text-sm"
                    >
                        {message}
                    </p>
                )}

                {auth.status === "anonymous" && (
                    <form onSubmit={handleSubmit} className="mt-6 space-y-5">
                        <div>
                            <Label
                                htmlFor="username"
                                className="mb-2 block text-sm"
                            >
                                用户名
                            </Label>
                            <Input
                                id="username"
                                name="username"
                                type="text"
                                autoComplete="username"
                                autoCapitalize="none"
                                spellCheck={false}
                                required
                                value={username}
                                onChange={(event) =>
                                    setUsername(event.target.value)
                                }
                                disabled={busy}
                                className="h-11"
                            />
                        </div>

                        <div>
                            <Label
                                htmlFor="password"
                                className="mb-2 block text-sm"
                            >
                                密码
                            </Label>
                            <Input
                                id="password"
                                name="password"
                                type="password"
                                autoComplete="current-password"
                                required
                                maxLength={128}
                                value={password}
                                onChange={(event) =>
                                    setPassword(event.target.value)
                                }
                                disabled={busy}
                                className="h-11"
                            />
                        </div>

                        <Button
                            type="submit"
                            disabled={busy}
                            className="w-full h-11"
                        >
                            {pending === "login" ? "正在登录…" : "登录"}
                        </Button>
                    </form>
                )}

                {auth.status === "authenticated" && (
                    <div className="mt-6 space-y-5">
                        <p>
                            当前账号：
                            <strong className="ml-2 break-all">
                                {auth.user.username}
                            </strong>
                        </p>

                        <Button
                            type="button"
                            onClick={() => void perform("logout")}
                            disabled={busy}
                            variant="outline"
                            className="w-full h-11"
                        >
                            {pending === "logout"
                                ? "正在退出…"
                                : "退出登录"}
                        </Button>
                    </div>
                )}

                {auth.status === "unknown" && (
                    <Button
                        type="button"
                        onClick={() => void perform("check")}
                        disabled={busy}
                        variant="outline"
                        className="mt-6 w-full h-11"
                    >
                        {pending === "check"
                            ? "正在查询…"
                            : "重新查询登录状态"}
                    </Button>
                )}

                <Link
                    href="/"
                    className="mt-6 inline-block self-start rounded-sm text-sm text-primary underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                    返回聊天首页
                </Link>
            </Card>
        </main>
    );
}