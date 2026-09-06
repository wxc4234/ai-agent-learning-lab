"use client";

import { useReducer, useRef, useState, type SubmitEvent } from "react";

import {
	chatReducer,
	initialChatState,
	toUserFacingError,
} from "../chat-state";

const REQUEST_TIMEOUT_MS = 30_000;

function statusLabel(status: string): string {
	switch (status) {
		case "thinking":
			return "正在思考……";
		case "streaming":
			return "正在生成……";
		case "done":
			return "已完成";
		case "aborted":
			return "已停止生成";
		case "error":
			return "生成失败";
		default:
			return "等待提问";
	}
}

export default function ChatPanel() {
	const [prompt, setPrompt] = useReducer(
		(_current: string, next: string) => next,
		"",
	);
	const [chatState, dispatch] = useReducer(chatReducer, initialChatState);
	const [activeRunId, setActiveRunId] = useState<string | null>(null);

	const controllerRef = useRef<AbortController | null>(null);
	const sessionIdRef = useRef<string | null>(null);

	const isBusy =
		chatState.status === "thinking" || chatState.status === "streaming";

	async function startRequest(requestPrompt: string, isRetry = false) {
		const controller = new AbortController();
		let didTimeout = false;
		const timeoutId = window.setTimeout(() => {
			didTimeout = true;
			controller.abort();
		}, REQUEST_TIMEOUT_MS);

		controllerRef.current = controller;
		if (isRetry) {
			dispatch({ type: "retry" });
		} else {
			sessionIdRef.current = crypto.randomUUID();
			dispatch({ type: "submit", prompt: requestPrompt });
		}

		try {
			const res = await fetch("/api/chat/stream", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					session_id: sessionIdRef.current,
					prompt: requestPrompt,
				}),
				signal: controller.signal,
			});

			const runId = res.headers.get("X-Run-ID");
			setActiveRunId(runId);
			if (!res.ok) {
				throw res;
			}
			if (!res.body) {
				throw new Error("服务端没有返回流式内容");
			}

			const reader = res.body
				.pipeThrough(new TextDecoderStream())
				.getReader();
			let hasStartedStreaming = false;

			while (true) {
				const { done, value } = await reader.read();
				if (done) {
					break;
				}

				if (!hasStartedStreaming) {
					hasStartedStreaming = true;
					dispatch({ type: "stream-start" });
				}
				dispatch({ type: "append", chunk: value });
			}

			dispatch({ type: "complete" });
		} catch (error) {
			if (error instanceof DOMException && error.name === "AbortError") {
				if (didTimeout) {
					dispatch({
						type: "fail",
						message: "请求超时了，请稍后重试。",
					});
				} else {
					dispatch({ type: "abort" });
				}
				return;
			}

			dispatch({ type: "fail", message: toUserFacingError(error) });
		} finally {
			window.clearTimeout(timeoutId);
			if (controllerRef.current === controller) {
				controllerRef.current = null;
			}
		}
	}

	async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
		event.preventDefault();
		const trimmedPrompt = prompt.trim();
		if (!trimmedPrompt || isBusy) {
			return;
		}

		await startRequest(trimmedPrompt);
	}

	async function handleRetry() {
		if (!chatState.lastPrompt || isBusy) {
			return;
		}

		await startRequest(chatState.lastPrompt, true);
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

                <div
                    aria-live="polite"
                    className="mt-4 text-sm text-zinc-500"
                    role="status"
                >
                    {statusLabel(chatState.status)}
                </div>

                {activeRunId && (
                    <p className="mt-1 text-xs text-zinc-400">
                        运行编号：{activeRunId}
                    </p>
                )}

                <form className="mt-4" onSubmit={handleSubmit}>
					<label
						className="text-sm font-medium text-zinc-700"
						htmlFor="prompt"
					>
						你的问题
					</label>

					<textarea
						id="prompt"
						className="mt-2 min-h-32 w-full resize-none rounded-xl border border-zinc-300 p-3 text-zinc-900 outline-none focus:border-zinc-500 disabled:bg-zinc-100"
						disabled={isBusy}
						placeholder="请输入问题……"
						value={prompt}
						onChange={(event) => setPrompt(event.target.value)}
					/>

					<div className="mt-3 flex items-center justify-end gap-3">
						{isBusy && (
							<button
								className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
								type="button"
								onClick={handleStop}
							>
								停止生成
							</button>
						)}

						{(chatState.status === "error" ||
							chatState.status === "aborted") && (
							<button
								className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
								type="button"
								onClick={handleRetry}
							>
								{chatState.status === "aborted"
									? "重新生成"
									: "重试"}
							</button>
						)}

						<button
							className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
							disabled={!prompt.trim() || isBusy}
							type="submit"
						>
							{isBusy ? "生成中……" : "发送"}
						</button>
					</div>
				</form>

				<div className="mt-6 min-h-32 rounded-xl bg-zinc-100 p-4">
					<p className="text-sm font-medium text-zinc-700">AI 回复</p>

					{chatState.errorMessage ? (
						<p
							aria-live="assertive"
							className="mt-2 text-red-700"
							role="alert"
						>
							{chatState.errorMessage}
						</p>
					) : (
						<p className="mt-2 whitespace-pre-wrap text-zinc-900">
							{chatState.reply || "回复将在这里逐步显示"}
						</p>
					)}
				</div>
			</section>
		</main>
	);
}
