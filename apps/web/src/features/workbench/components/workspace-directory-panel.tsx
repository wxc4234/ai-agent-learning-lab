"use client";

import {
    useCallback,
    useEffect,
    useId,
    useRef,
    useState,
} from "react";

import { Button } from "@/components/ui/button";

type DirectoryState =
    | { status: "loading" }
    | { status: "error" }
    | { status: "ready"; rootPath: string | null; message?: string }
    | { status: "saving" }
    | { status: "uncertain" };

type DirectoryData = {
    rootPath: string | null;
};

const PATH_ERRORS: Record<string, string> = {
    directory_picker_busy: "已有目录选择窗口，请先完成或取消选择。",
    directory_picker_timeout: "目录选择已超时，请重新选择。",
    directory_picker_unsupported: "当前系统暂不支持目录选择。",
    directory_picker_unavailable: "无法打开系统目录选择窗口，请检查本地桌面环境。",
    invalid_workspace_input: "请求参数不符合要求，请检查输入。",
    invalid_directory_path: "所选目录路径不符合要求，请重新选择。",
    directory_not_found: "目录不存在，请检查路径。",
    directory_access_denied: "没有权限访问这个目录。",
    not_a_directory: "这个路径是文件，请选择目录。",
    root_directory_not_allowed: "不能绑定文件系统根目录。",
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value)
    );
}

function readDirectory(
    value: unknown,
    workspaceId: string,
): DirectoryData | null {
    // 响应必须属于当前项目；缺字段不能被解释为未绑定。
    if (
        !isRecord(value) ||
        value.external_id !== workspaceId ||
        typeof value.name !== "string" ||
        Array.from(value.name).length < 1 ||
        Array.from(value.name).length > 100 ||
        !Object.hasOwn(value, "root_path") ||
        (
            value.root_path !== null &&
            (
                typeof value.root_path !== "string" ||
                value.root_path.length === 0 ||
                value.root_path.includes("\0")
            )
        )
    ) {
        return null;
    }

    // 平台相关的绝对路径检查由 BFF 和后端负责。
    return { rootPath: value.root_path as string | null };
}

export default function WorkspaceDirectoryPanel({
    workspaceId,
}: {
    workspaceId: string;
}) {
    const inputId = useId();
    const [state, setState] = useState<DirectoryState>({
        status: "loading",
    });
    const activeRequest = useRef<AbortController | null>(null);

    const execute = useCallback(async (writing = false) => {
        // ref 同步占位，避免按钮尚未更新时连续发出两个请求。
        if (activeRequest.current !== null) {
            return;
        }

        const controller = new AbortController();
        const signal = AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(writing ? 140_000 : 15_000),
        ]);

        activeRequest.current = controller;
        setState({ status: writing ? "saving" : "loading" });

        const isCurrent = () =>
            activeRequest.current === controller &&
            !controller.signal.aborted;

        try {
            const response = await fetch(
                `/api/workspaces/${workspaceId}/directory${writing ? "/select" : ""}`,
                {
                    method: writing ? "POST" : "GET",
                    credentials: "same-origin",
                    cache: "no-store",
                    signal,
                    ...(writing
                        ? {
                            headers: {
                                "Content-Type": "application/json",
                            },
                            body: "{}",
                        }
                        : {}),
                },
            );

            // 系统窗口取消不写入，恢复按钮；不把 204 当作畸形 JSON。
            if (writing && response.status === 204) {
                signal.throwIfAborted();
                if (isCurrent()) {
                    setState({ status: "ready", rootPath: null, message: "已取消选择，项目未作更改。" });
                }
                return;
            }

            const payload: unknown = await response.json();

            // 超时也覆盖正文读取；旧项目的响应不能更新当前组件。
            signal.throwIfAborted();

            if (!isCurrent()) {
                return;
            }

            // 系统窗口或目录校验明确失败时，允许重新选择。
            // 使用本地文案，不直接渲染接口返回的任意 message。
            if (
                writing &&
                [408, 409, 422, 501, 503].includes(response.status) &&
                isRecord(payload) &&
                typeof payload.code === "string" &&
                Object.hasOwn(PATH_ERRORS, payload.code)
            ) {
                setState({
                    status: "ready",
                    rootPath: null,
                    message: PATH_ERRORS[payload.code],
                });
                return;
            }

            if (response.status !== 200) {
                throw new Error("Directory request failed");
            }

            const data = readDirectory(payload, workspaceId);

            // 选择请求成功必须返回实际路径，不能返回未绑定状态。
            if (data === null || (writing && data.rootPath === null)) {
                throw new Error("Invalid directory response");
            }

            setState({
                status: "ready",
                rootPath: data.rootPath,
            });

        } catch {
            if (!isCurrent()) {
                return;
            }

            // GET 失败显示读取错误；选择请求异常保守处理为结果未确认。
            // 不通过清空路径或自动重发来猜测服务端是否已经提交。
            setState({
                status: writing ? "uncertain" : "error",
            });
        } finally {
            if (activeRequest.current === controller) {
                activeRequest.current = null;
            }
        }
    }, [workspaceId]);

    useEffect(() => {
        let disposed = false;

        // 避免 Strict Mode 首次清理后启动失效的初始化请求。
        queueMicrotask(() => {
            if (!disposed) {
                void execute();
            }
        });

        return () => {
            disposed = true;

            const controller = activeRequest.current;
            activeRequest.current = null;
            controller?.abort();
        };
    }, [execute]);

    const busy =
        state.status === "loading" ||
        state.status === "saving";

    return (
        <section
            aria-labelledby={`${inputId}-title`}
            aria-busy={busy}
            className="mt-4 min-w-0 border-t border-border pt-3"
        >
            <h4
                id={`${inputId}-title`}
                className="text-xs font-medium"
            >
                项目目录
            </h4>

            {state.status === "loading" && (
                <p
                    role="status"
                    className="mt-2 text-xs text-muted-foreground"
                >
                    正在读取目录状态…
                </p>
            )}

            {state.status === "saving" && (
                <p
                    role="status"
                    className="mt-2 text-xs text-muted-foreground"
                >
                    请在系统窗口中选择项目目录…
                </p>
            )}

            {(state.status === "error" ||
                state.status === "uncertain") && (
                <div className="mt-2 space-y-2">
                    <p role="alert" className="text-xs text-destructive">
                        {state.status === "uncertain"
                            ? "目录选择结果未确认，请重新读取当前保存状态。"
                            : "目录状态读取失败，请重试。"}
                    </p>

                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void execute()}
                    >
                        重新读取
                    </Button>
                </div>
            )}

            {state.status === "ready" && (
                state.rootPath !== null ? (
                    <div className="mt-2 space-y-2">
                        <p role="status" className="text-xs">
                            已绑定
                        </p>

                        <p className="break-all font-mono text-xs">
                            {state.rootPath}
                        </p>

                        <p className="text-xs text-muted-foreground">
                            这是已保存的绑定路径，不代表目录当前仍可访问。
                        </p>

                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => void execute()}
                        >
                            刷新目录状态
                        </Button>
                    </div>
                ) : (
                    <div className="mt-2 space-y-3">
                        <p role="status" className="text-xs text-muted-foreground">
                            选择本机文件夹作为项目目录。
                        </p>
                        {state.message && (
                            <p role="status" className="text-xs text-muted-foreground">
                                {state.message}
                            </p>
                        )}
                        {/* 选择后由本地服务直接校验并保存，不再要求额外点击绑定。 */}
                        <Button type="button" size="sm" onClick={() => void execute(true)}>
                            选择项目目录
                        </Button>
                    </div>
                )
            )}
        </section>
    );
}
