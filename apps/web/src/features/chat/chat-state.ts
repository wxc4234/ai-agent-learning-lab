// 状态机复用协议边界已经校验过的指标类型。
// import type 不会产生运行时代码或模块依赖。
import type { AgentRunMetrics } from "./agent-stream";

export type ChatStatus =
	| "idle"
	| "thinking"
	| "streaming"
	| "done"
	| "aborted"
	| "error";

export type ToolActivity = {
	toolCallId: string;
	toolName: string;
	arguments: string;
	status: "running" | "succeeded" | "failed";
	result?: string;
	errorMessage?: string;
	// undefined 表示仍在运行，null 表示工具尚未执行就失败
	durationMs?: number | null;
};

// 保存同一个运行终态事件中的步骤数与指标
export type CompletedRunSummary = {
	stepsTaken: number;
	metrics: AgentRunMetrics;
};

export type ChatState = {
	status: ChatStatus;
	reply: string;
	errorMessage: string | null;
	lastPrompt: string;
	tools: ToolActivity[];

	// 正常完成或指标型失败时保存摘要；其他路径为 null
	runSummary: CompletedRunSummary | null;
};

export type ChatAction =
	| { type: "submit"; prompt: string }
	| {
			type: "tool-start";
			toolCallId: string;
			toolName: string;
			arguments: string;
	  }
	| {
			type: "tool-result";
			toolCallId: string;
			result: string;
			durationMs: number;
	  }
	| {
			type: "tool-error";
			toolCallId: string;
			message: string;
			durationMs: number | null;
	  }
	| { type: "stream-start" }
	| { type: "append"; chunk: string }
	// 完成动作必须携带同一个终态事件中的完整数据。
	| {
			type: "complete";
			stepsTaken: number;
			metrics: AgentRunMetrics;
	  }
	| { type: "abort" }
	| {
		type: "fail";
		message: string;
		summary?: CompletedRunSummary;
	  }
	| { type: "retry" }
	| { type: "reset" };

export const initialChatState: ChatState = {
	status: "idle",
	reply: "",
	errorMessage: null,
	lastPrompt: "",
	tools: [],

	// 初始状态还没有完成指标。
	runSummary: null,
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
	switch (action.type) {
		case "submit":
			return {
				status: "thinking",
				reply: "",
				errorMessage: null,
				lastPrompt: action.prompt,
				tools: [],

				// 新请求不能继续携带上一轮指标。
				runSummary: null,
			};
		case "tool-start":
			return {
				...state,
				tools: [
					...state.tools,
					{
						toolCallId: action.toolCallId,
						toolName: action.toolName,
						arguments: action.arguments,
						status: "running",
					},
				],
			};
		case "tool-result":
			return {
				...state,
				tools: state.tools.map((tool) =>
					tool.toolCallId === action.toolCallId
						? {
								...tool,
								status: "succeeded",
								result: action.result,
								errorMessage: undefined,
								durationMs: action.durationMs,
							}
						: tool,
				),
			};
		case "tool-error":
			return {
				...state,
				tools: state.tools.map((tool) =>
					tool.toolCallId === action.toolCallId
						? {
								...tool,
								status: "failed",
								errorMessage: action.message,
								result: undefined,
								durationMs: action.durationMs,
							}
						: tool,
				),
			};
		case "stream-start":
			return { ...state, status: "streaming", errorMessage: null };
		case "append":
			return {
				...state,
				status: "streaming",
				reply: state.reply + action.chunk,
				errorMessage: null,
			};
		case "complete":
			// 终态和完成摘要在同一次 reducer 更新中写入。
			return {
				...state,
				status: "done",
				errorMessage: null,
				runSummary: {
					stepsTaken: action.stepsTaken,
					metrics: action.metrics,
				},
			};
		case "abort":
			return {
				...state,
				status: "aborted",
				errorMessage: null,

				// 取消不属于正常完成，不能留下完成指标。
				runSummary: null,
			};
		case "fail":
			return {
				...state,
				status: "error",
				errorMessage: action.message,

				// 保存本次失败携带的摘要；未提供时明确清空
				runSummary: action.summary ?? null,
			};
		case "retry":
			return {
				...state,
				status: "thinking",
				reply: "",
				errorMessage: null,
				tools: [],

				// 重试是一轮新运行，必须清除旧摘要。
				runSummary: null,
			};
		case "reset":
			return initialChatState;
		default:
			// 穷尽性检查：action 已被所有 case 覆盖，此处 action 收窄为 never。
			// 将来新增 action 类型却漏写 case 时，这一行会编译报错。
			return action;
	}
}

export function toUserFacingError(error: unknown): string {
    if (error instanceof Response) {
        if (error.status === 401) {
            return "登录状态已失效，请打开“账号与退出”重新登录。";
        }

        if (error.status === 403) {
            return "聊天请求来源不被允许，请从配置的应用地址访问。";
        }

		if (error.status === 404) {
            return "会话不存在或不可访问，请重新发送问题以创建新会话。";
        }

        if (
            error.status === 400 ||
            error.status === 415 ||
            error.status === 422
        ) {
            return "聊天请求不符合要求，请检查输入后再试。";
        }

        if (error.status === 409) {
            return "该会话仍在执行或收尾，请稍后再试";
        }

        if (error.status === 429) {
            return "请求太频繁了，请稍后再试。";
        }

		if (error.status === 502) {
            return "模型服务暂时不可用，请稍后重试。";
        }

        if (error.status === 503) {
            return "执行服务暂时繁忙或不可用，请稍后再试。";
        }

        if (error.status >= 500) {
            return "服务暂时出错，请稍后重试。";
        }
    }

    if (error instanceof TypeError) {
        return "网络连接中断，请检查网络后重试。";
    }

    if (error instanceof Error && error.message) {
        return error.message;
    }

    return "发生了未知错误，请稍后重试。";
}