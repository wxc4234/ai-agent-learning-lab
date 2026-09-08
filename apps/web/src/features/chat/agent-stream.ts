export type AgentStreamEvent =
	| {
			type: "TOOL_CALL_START";
			tool_call_id: string;
			tool_name: string;
			arguments: string;
	  }
	| {
			type: "TOOL_CALL_RESULT";
			tool_call_id: string;
			tool_name: string;
			result: string;
	  }
	| {
			type: "TOOL_CALL_ERROR";
			tool_call_id: string;
			tool_name: string;
			code: string;
			message: string;
			details?: string;
	  }
	| { type: "TEXT_MESSAGE_START" }
	| { type: "TEXT_MESSAGE_CONTENT"; chunk: string }
	| { type: "TEXT_MESSAGE_END" }
	| { type: "RUN_FINISHED"; steps_taken: number }
	| { type: "RUN_ERROR"; code: string; message: string };

type JsonObject = Record<string, unknown>;

function isJsonObject(value: unknown): value is JsonObject {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(event: JsonObject, field: string): string {
	const value = event[field];
	if (typeof value !== "string") {
		throw new Error(`Agent 事件缺少字符串字段：${field}`);
	}
	return value;
}

function readNumber(event: JsonObject, field: string): number {
	const value = event[field];
	if (typeof value !== "number" || !Number.isFinite(value)) {
		throw new Error(`Agent 事件缺少数字字段：${field}`);
	}
	return value;
}

export function parseAgentStreamLine(line: string): AgentStreamEvent {
	let parsed: unknown;
	try {
		parsed = JSON.parse(line);
	} catch {
		throw new Error("服务端返回了无效的 Agent 事件");
	}

	if (!isJsonObject(parsed) || typeof parsed.type !== "string") {
		throw new Error("服务端返回了无效的 Agent 事件");
	}

	switch (parsed.type) {
		case "TOOL_CALL_START":
			return {
				type: parsed.type,
				tool_call_id: readString(parsed, "tool_call_id"),
				tool_name: readString(parsed, "tool_name"),
				arguments: readString(parsed, "arguments"),
			};
		case "TOOL_CALL_RESULT":
			return {
				type: parsed.type,
				tool_call_id: readString(parsed, "tool_call_id"),
				tool_name: readString(parsed, "tool_name"),
				result: readString(parsed, "result"),
			};
		case "TOOL_CALL_ERROR": {
			const details = parsed.details;
			if (details !== undefined && typeof details !== "string") {
				throw new Error("Agent 事件字段类型错误：details");
			}
			return {
				type: parsed.type,
				tool_call_id: readString(parsed, "tool_call_id"),
				tool_name: readString(parsed, "tool_name"),
				code: readString(parsed, "code"),
				message: readString(parsed, "message"),
				...(details === undefined ? {} : { details }),
			};
		}
		case "TEXT_MESSAGE_START":
		case "TEXT_MESSAGE_END":
			return { type: parsed.type };
		case "TEXT_MESSAGE_CONTENT":
			return {
				type: parsed.type,
				chunk: readString(parsed, "chunk"),
			};
		case "RUN_FINISHED":
			return {
				type: parsed.type,
				steps_taken: readNumber(parsed, "steps_taken"),
			};
		case "RUN_ERROR":
			return {
				type: parsed.type,
				code: readString(parsed, "code"),
				message: readString(parsed, "message"),
			};
		default:
			throw new Error(`无法识别的 Agent 事件：${parsed.type}`);
	}
}

export async function* readAgentStream(
	stream: ReadableStream<Uint8Array>,
): AsyncGenerator<AgentStreamEvent> {
	const reader = stream.getReader();
	const decoder = new TextDecoder();
	let buffer = "";

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) {
				buffer += decoder.decode();
				break;
			}

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split("\n");
			buffer = lines.pop() ?? "";

			for (const line of lines) {
				const normalizedLine = line.trim();
				if (normalizedLine) {
					yield parseAgentStreamLine(normalizedLine);
				}
			}
		}

		const finalLine = buffer.trim();
		if (finalLine) {
			yield parseAgentStreamLine(finalLine);
		}
	} finally {
		reader.releaseLock();
	}
}
