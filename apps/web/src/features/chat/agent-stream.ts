// 描述模型累计 Token 用量。
export type AgentModelUsage = {
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	cache_hit_input_tokens: number | null;
	cache_miss_input_tokens: number | null;
};

// 保存本次费用计算采用的价格快照。
export type AgentPricingSnapshot = {
	model: string;
	tier: "peak" | "off_peak";
	cache_hit_input_cny_per_million: string;
	cache_miss_input_cny_per_million: string;
	output_cny_per_million: string;
};

// 描述 RUN_FINISHED 携带的运行指标。
export type AgentRunMetrics = {
	model_usage: AgentModelUsage | null;
	model_duration_ms: number | null;
	tool_duration_ms: number;
	estimated_cost_cny: string | null;
	pricing: AgentPricingSnapshot;
};

// 浏览器只接受 Runtime 当前定义的四种稳定工具错误码。
export type AgentToolErrorCode =
	| "unknown_tool"
	| "invalid_tool_arguments"
	| "tool_execution_failed"
	| "tool_timeout";

// 普通错误没有运行摘要；Agent Loop 终态错误必须携带完整摘要。
export type AgentRunErrorEvent =
	| {
			type: "RUN_ERROR";
			code: string;
			message: string;
			steps_taken?: never;
			metrics?: never;
	  }
	| {
			type: "RUN_ERROR";
			code: string;
			message: string;
			steps_taken: number;
			metrics: AgentRunMetrics;
	  };

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

			// 成功事件一定真正执行过工具。
			duration_ms: number;
	  }
	| {
			type: "TOOL_CALL_ERROR";
			tool_call_id: string;
			tool_name: string;
			code: AgentToolErrorCode;
			message: string;
			details?: string;

			// 是否为空由错误发生阶段决定。
			duration_ms: number | null;
	  }
	| { type: "TEXT_MESSAGE_START" }
	| { type: "TEXT_MESSAGE_CONTENT"; chunk: string }
	| { type: "TEXT_MESSAGE_END" }
	| {
			type: "RUN_FINISHED";
			steps_taken: number;
			metrics: AgentRunMetrics;
	  }
	| AgentRunErrorEvent;

type JsonObject = Record<string, unknown>;

// 只接受后端 Decimal 转换出的非负普通十进制字符串。
// 接受 "0"、"3.0"、"0.00044300"，拒绝负数、指数和特殊值。
const NON_NEGATIVE_DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

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

// Token、步骤数和耗时都必须是浏览器可安全表示的非负整数。
function readNonNegativeInteger(event: JsonObject, field: string): number {
	const value = event[field];
	if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
		throw new Error(`Agent 事件字段必须是非负整数：${field}`);
	}
	return value;
}

// 只接受明确的 null；字段缺失得到 undefined，仍会校验失败。
function readNullableNonNegativeInteger(
	event: JsonObject,
	field: string,
): number | null {
	if (event[field] === null) {
		return null;
	}
	return readNonNegativeInteger(event, field);
}

// 校验并收窄工具错误码。
function readToolErrorCode(event: JsonObject): AgentToolErrorCode {
	const code = readString(event, "code");

	switch (code) {
		case "unknown_tool":
		case "invalid_tool_arguments":
		case "tool_execution_failed":
		case "tool_timeout":
			return code;
		default:
			throw new Error(`无法识别的工具错误代码：${code}`);
	}
}

// 错误码是内层判别字段，决定耗时必须是整数还是 null。
function readToolErrorDuration(
	event: JsonObject,
	code: AgentToolErrorCode,
): number | null {
	switch (code) {
		case "tool_execution_failed":
		case "tool_timeout":
			return readNonNegativeInteger(event, "duration_ms");

		case "unknown_tool":
		case "invalid_tool_arguments":
			// undefined 也不等于 null，因此字段缺失会被拒绝。
			if (event.duration_ms !== null) {
				throw new Error(
					"执行前工具错误的 duration_ms 必须明确为 null",
				);
			}
			return null;
	}
}

// 金额以字符串形式校验和保存，不转换成 JavaScript number。
function readNonNegativeDecimalString(
	event: JsonObject,
	field: string,
): string {
	const value = event[field];
	if (
		typeof value !== "string" ||
		!NON_NEGATIVE_DECIMAL_PATTERN.test(value)
	) {
		throw new Error(`Agent 事件字段必须是非负十进制字符串：${field}`);
	}
	return value;
}

// 费用允许明确为 null，但不允许字段缺失。
function readNullableNonNegativeDecimalString(
	event: JsonObject,
	field: string,
): string | null {
	if (event[field] === null) {
		return null;
	}
	return readNonNegativeDecimalString(event, field);
}

// 校验可空的 model_usage 及其全部嵌套字段。
function readModelUsage(metrics: JsonObject): AgentModelUsage | null {
	const value = metrics.model_usage;

	if (value === null) {
		return null;
	}
	if (!isJsonObject(value)) {
		throw new Error("Agent 事件字段类型错误：model_usage");
	}

	return {
		input_tokens: readNonNegativeInteger(value, "input_tokens"),
		output_tokens: readNonNegativeInteger(value, "output_tokens"),
		total_tokens: readNonNegativeInteger(value, "total_tokens"),
		cache_hit_input_tokens: readNullableNonNegativeInteger(
			value,
			"cache_hit_input_tokens",
		),
		cache_miss_input_tokens: readNullableNonNegativeInteger(
			value,
			"cache_miss_input_tokens",
		),
	};
}

// 校验价格快照对象、时段枚举与三个精确金额字符串。
function readPricingSnapshot(metrics: JsonObject): AgentPricingSnapshot {
	const value = metrics.pricing;

	if (!isJsonObject(value)) {
		throw new Error("Agent 事件字段类型错误：pricing");
	}

	const tier = readString(value, "tier");
	if (tier !== "peak" && tier !== "off_peak") {
		throw new Error("Agent 事件字段值错误：tier");
	}

	return {
		model: readString(value, "model"),
		tier,
		cache_hit_input_cny_per_million: readNonNegativeDecimalString(
			value,
			"cache_hit_input_cny_per_million",
		),
		cache_miss_input_cny_per_million: readNonNegativeDecimalString(
			value,
			"cache_miss_input_cny_per_million",
		),
		output_cny_per_million: readNonNegativeDecimalString(
			value,
			"output_cny_per_million",
		),
	};
}

// metrics 自身必须存在且必须是普通 JSON 对象。
function readRunMetrics(event: JsonObject): AgentRunMetrics {
	const value = event.metrics;

	if (!isJsonObject(value)) {
		throw new Error("Agent 事件字段类型错误：metrics");
	}

	return {
		model_usage: readModelUsage(value),
		model_duration_ms: readNullableNonNegativeInteger(
			value,
			"model_duration_ms",
		),
		tool_duration_ms: readNonNegativeInteger(value, "tool_duration_ms"),
		estimated_cost_cny: readNullableNonNegativeDecimalString(
			value,
			"estimated_cost_cny",
		),
		pricing: readPricingSnapshot(value),
	};
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

				// 成功事件必须携带非负安全整数耗时。
				duration_ms: readNonNegativeInteger(parsed, "duration_ms"),
			};
		case "TOOL_CALL_ERROR": {
			const details = parsed.details;
			if (details !== undefined && typeof details !== "string") {
				throw new Error("Agent 事件字段类型错误：details");
			}

			// 先校验 code，再用它校验 duration_ms。
			const code = readToolErrorCode(parsed);

			return {
				type: parsed.type,
				tool_call_id: readString(parsed, "tool_call_id"),
				tool_name: readString(parsed, "tool_name"),
				code,
				message: readString(parsed, "message"),
				duration_ms: readToolErrorDuration(parsed, code),
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
				steps_taken: readNonNegativeInteger(parsed, "steps_taken"),
				metrics: readRunMetrics(parsed),
			};
		case "RUN_ERROR": {
			const code = readString(parsed, "code");
			const message = readString(parsed, "message");
			const hasStepsTaken = parsed.steps_taken !== undefined;
			const hasMetrics = parsed.metrics !== undefined;

			// 错误摘要只能完整出现，不能只携带其中一半。
			if (hasStepsTaken !== hasMetrics) {
				throw new Error(
					"RUN_ERROR 的 steps_taken 与 metrics 必须同时存在",
				);
			}

			if (!hasStepsTaken) {
				return {
					type: parsed.type,
					code,
					message,
				};
			}

			return {
				type: parsed.type,
				code,
				message,
				steps_taken: readNonNegativeInteger(parsed, "steps_taken"),
				metrics: readRunMetrics(parsed),
			};
		}
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
