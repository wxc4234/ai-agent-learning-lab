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
};

export type ChatState = {
	status: ChatStatus;
	reply: string;
	errorMessage: string | null;
	lastPrompt: string;
	tools: ToolActivity[];
};

export type ChatAction =
	| { type: "submit"; prompt: string }
	| {
			type: "tool-start";
			toolCallId: string;
			toolName: string;
			arguments: string;
	  }
	| { type: "tool-result"; toolCallId: string; result: string }
	| { type: "tool-error"; toolCallId: string; message: string }
	| { type: "stream-start" }
	| { type: "append"; chunk: string }
	| { type: "complete" }
	| { type: "abort" }
	| { type: "fail"; message: string }
	| { type: "retry" }
	| { type: "reset" };

export const initialChatState: ChatState = {
	status: "idle",
	reply: "",
	errorMessage: null,
	lastPrompt: "",
	tools: [],
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
			return { ...state, status: "done", errorMessage: null };
		case "abort":
			return { ...state, status: "aborted", errorMessage: null };
		case "fail":
			return { ...state, status: "error", errorMessage: action.message };
		case "retry":
			return {
				...state,
				status: "thinking",
				reply: "",
				errorMessage: null,
				tools: [],
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
		if (error.status === 429) {
			return "请求太频繁了，请稍后再试。";
		}
		if (error.status === 502) {
			return "模型服务暂时不可用，请稍后重试。";
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
