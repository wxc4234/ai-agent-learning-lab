"use client";

import {useRef, useState, type SubmitEvent} from "react";

export default function ChatPanel() {
    const [prompt, setPrompt] = useState("");
    const [reply, setReply] = useState("");
    const [isStreaming, setIsStreaming] = useState(false);

    const controllerRef = useRef<AbortController | null>(null);
    const sessionIdRef = useRef<string | null>(null);

    async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault();
        const trimmedPrompt = prompt.trim();
        if (!trimmedPrompt || isStreaming){
            return;
        }

        const controller = new AbortController();
        controllerRef.current = controller;
        sessionIdRef.current = crypto.randomUUID();

        setReply("");
        setIsStreaming(true);

        try{
            const res = await fetch("/api/chat/stream", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    session_id: sessionIdRef.current,
                    prompt: trimmedPrompt
                }),
                signal: controller.signal,
            });

            if (!res.ok) {
                const errorMessage = await res.text();
                throw new Error(errorMessage || "聊天服务暂时不可用");
            }
            if (!res.body) {
                throw new Error("服务端没有返回流式内容");
            }

            // 读取流
            const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();

            while (true) {
                const {done, value} = await reader.read();
                if (done) {
                    break;
                }

                setReply((currentReply) => currentReply + value);
            }
        }
        catch (error) {
             if (error instanceof DOMException && error.name === "AbortError") {
                return;
            }

            const message =
                error instanceof Error
                    ? error.message
                    : "发生了未知错误";

            setReply(`请求失败：${message}`);
        }
        finally {
            if (controllerRef.current === controller) {
                controllerRef.current = null;
                setIsStreaming(false);
            }
        }
    }
    function handleStop() {
        controllerRef.current?.abort();
    }


    return (
        <main className="flex min-h-screen items-center justify-center bg-zinc-50 px-6">
            <section className="w-full max-w-2xl rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm">
                <h1 className="text-2xl font-semibold text-zinc-900">
                    AI Agent
                </h1>

                <p className="mt-2 text-sm text-zinc-500">
                    通过 Next.js BFF 流式调用 FastAPI。
                </p>

                <form className="mt-6" onSubmit={handleSubmit}>
                    <label
                        className="text-sm font-medium text-zinc-700"
                        htmlFor="prompt"
                    >
                        你的问题
                    </label>

                    <textarea
                        id="prompt"
                        className="mt-2 min-h-32 w-full resize-none rounded-xl border border-zinc-300 p-3 text-zinc-900 outline-none focus:border-zinc-500 disabled:bg-zinc-100"
                        disabled={isStreaming}
                        placeholder="请输入问题……"
                        value={prompt}
                        onChange={(event) => setPrompt(event.target.value)}
                    />

                    <div className="mt-3 flex items-center justify-end gap-3">
                        {isStreaming && (
                            <button
                                className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
                                type="button"
                                onClick={handleStop}
                            >
                                停止生成
                            </button>
                        )}

                        <button
                            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                            disabled={!prompt.trim() || isStreaming}
                            type="submit"
                        >
                            {isStreaming ? "生成中……" : "发送"}
                        </button>
                    </div>
                </form>

                <div className="mt-6 min-h-32 rounded-xl bg-zinc-100 p-4">
                    <p className="text-sm font-medium text-zinc-700">
                        AI 回复
                    </p>

                    <p className="mt-2 whitespace-pre-wrap text-zinc-900">
                        {reply || "回复将在这里逐步显示"}
                    </p>
                </div>
            </section>
        </main>
    );
}