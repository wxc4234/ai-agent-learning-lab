import assert from "node:assert/strict";
import { test } from "node:test";

import {
	parseAgentStreamLine,
	readAgentStream,
	type AgentStreamEvent,
} from "../../../src/features/chat/agent-stream.ts";

function streamFromByteChunks(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
	return new ReadableStream({
		start(controller) {
			for (const chunk of chunks) {
				controller.enqueue(chunk);
			}
			controller.close();
		},
	});
}

function createRunFinishedEvent() {
	return {
		type: "RUN_FINISHED",
		steps_taken: 2,
		metrics: {
			model_usage: {
				input_tokens: 120,
				output_tokens: 35,
				total_tokens: 155,
				cache_hit_input_tokens: 80,
				cache_miss_input_tokens: 40,
			},
			model_duration_ms: 1840,
			tool_duration_ms: 12,
			estimated_cost_cny: "0.00044300",
			pricing: {
				model: "deepseek-chat",
				tier: "peak",
				cache_hit_input_cny_per_million: "0.10",
				cache_miss_input_cny_per_million: "3.0",
				output_cny_per_million: "9.0",
			},
		},
	};
}

function createToolResultEvent() {
	return {
		type: "TOOL_CALL_RESULT",
		tool_call_id: "call-area",
		tool_name: "calculate_rectangle_area",
		result: "面积是 12",
		duration_ms: 12,
	};
}

function createToolErrorEvent(code: string, durationMs: unknown) {
	return {
		type: "TOOL_CALL_ERROR",
		tool_call_id: "call-error",
		tool_name: "unstable_tool",
		code,
		message: "工具调用失败",
		duration_ms: durationMs,
	};
}

function createRunErrorEvent() {
	const finishedEvent = createRunFinishedEvent();

	return {
		type: "RUN_ERROR",
		code: "token_budget_exhausted",
		message: "本次运行已达到 Token 预算上限",
		steps_taken: finishedEvent.steps_taken,
		metrics: finishedEvent.metrics,
	};
}

function withoutField(
	value: Record<string, unknown>,
	field: string,
): Record<string, unknown> {
	return Object.fromEntries(
		Object.entries(value).filter(([key]) => key !== field),
	);
}

test("readAgentStream handles events split across arbitrary byte chunks", async () => {
	const body = [
		JSON.stringify({
			type: "TOOL_CALL_START",
			tool_call_id: "call-area",
			tool_name: "calculate_rectangle_area",
			arguments: '{"width": 3, "height": 4}',
		}),
		JSON.stringify(createToolResultEvent()),
		JSON.stringify({
			type: "TEXT_MESSAGE_CONTENT",
			chunk: "矩形面积是 12。",
		}),
		JSON.stringify(createRunFinishedEvent()),
	].join("\n");
	const encoded = new TextEncoder().encode(body);
	const stream = streamFromByteChunks([
		encoded.slice(0, 17),
		encoded.slice(17, 63),
		encoded.slice(63, 101),
		encoded.slice(101),
	]);
	const events: AgentStreamEvent[] = [];

	for await (const event of readAgentStream(stream)) {
		events.push(event);
	}

	assert.deepEqual(
		events.map((event) => event.type),
		[
			"TOOL_CALL_START",
			"TOOL_CALL_RESULT",
			"TEXT_MESSAGE_CONTENT",
			"RUN_FINISHED",
		],
	);
	assert.equal(events[1]?.type, "TOOL_CALL_RESULT");
	if (events[1]?.type === "TOOL_CALL_RESULT") {
		assert.equal(events[1].result, "面积是 12");
		assert.equal(events[1].duration_ms, 12);
	}
});

test("parseAgentStreamLine parses tool result duration", () => {
	const event = parseAgentStreamLine(JSON.stringify(createToolResultEvent()));

	assert.equal(event.type, "TOOL_CALL_RESULT");
	if (event.type === "TOOL_CALL_RESULT") {
		assert.equal(event.duration_ms, 12);
	}
});

test("parseAgentStreamLine parses execution tool error durations", () => {
	const cases = [
		["tool_execution_failed", 7],
		["tool_timeout", 11],
	] as const;

	for (const [code, durationMs] of cases) {
		const event = parseAgentStreamLine(
			JSON.stringify(createToolErrorEvent(code, durationMs)),
		);

		assert.equal(event.type, "TOOL_CALL_ERROR");
		if (event.type === "TOOL_CALL_ERROR") {
			assert.equal(event.code, code);
			assert.equal(event.duration_ms, durationMs);
		}
	}
});

test("parseAgentStreamLine accepts null duration for pre-execution tool errors", () => {
	const codes = ["unknown_tool", "invalid_tool_arguments"] as const;

	for (const code of codes) {
		const event = parseAgentStreamLine(
			JSON.stringify(createToolErrorEvent(code, null)),
		);

		assert.equal(event.type, "TOOL_CALL_ERROR");
		if (event.type === "TOOL_CALL_ERROR") {
			assert.equal(event.code, code);
			assert.equal(event.duration_ms, null);
		}
	}
});

test("parseAgentStreamLine rejects invalid tool result durations", () => {
	const completeEvent = createToolResultEvent();
	const invalidEvents = [
		withoutField(completeEvent, "duration_ms"),
		{ ...completeEvent, duration_ms: null },
		{ ...completeEvent, duration_ms: -1 },
		{ ...completeEvent, duration_ms: 1.5 },
		{ ...completeEvent, duration_ms: true },
		{ ...completeEvent, duration_ms: Number.MAX_SAFE_INTEGER + 1 },
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(
			() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
			/非负整数：duration_ms/,
		);
	}
});

test("parseAgentStreamLine rejects invalid execution tool error durations", () => {
	const completeEvent = createToolErrorEvent("tool_execution_failed", 7);
	const invalidEvents = [
		withoutField(completeEvent, "duration_ms"),
		{ ...completeEvent, duration_ms: null },
		{ ...completeEvent, duration_ms: -1 },
		{ ...completeEvent, duration_ms: 1.5 },
		{ ...completeEvent, duration_ms: true },
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(
			() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
			/非负整数：duration_ms/,
		);
	}
});

test("parseAgentStreamLine rejects missing or numeric pre-execution duration", () => {
	const completeEvent = createToolErrorEvent("unknown_tool", null);
	const invalidEvents = [
		withoutField(completeEvent, "duration_ms"),
		{ ...completeEvent, duration_ms: 0 },
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(
			() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
			/duration_ms 必须明确为 null/,
		);
	}
});

test("parseAgentStreamLine rejects unknown tool error codes", () => {
	assert.throws(
		() =>
			parseAgentStreamLine(
				JSON.stringify(createToolErrorEvent("new_tool_error", null)),
			),
		/无法识别的工具错误代码/,
	);
});

test("parseAgentStreamLine keeps a basic RUN_ERROR backward compatible", () => {
	const event = parseAgentStreamLine(
		JSON.stringify({
			type: "RUN_ERROR",
			code: "model_unavailable",
			message: "模型服务暂时不可用",
		}),
	);

	assert.deepEqual(event, {
		type: "RUN_ERROR",
		code: "model_unavailable",
		message: "模型服务暂时不可用",
	});
});

test("parseAgentStreamLine parses a complete RUN_ERROR summary", () => {
	const event = parseAgentStreamLine(JSON.stringify(createRunErrorEvent()));

	assert.equal(event.type, "RUN_ERROR");
	if (event.type === "RUN_ERROR" && event.metrics !== undefined) {
		assert.equal(event.steps_taken, 2);
		assert.equal(event.metrics.model_usage?.total_tokens, 155);
		assert.equal(event.metrics.estimated_cost_cny, "0.00044300");
	}
});

test("parseAgentStreamLine rejects a partial RUN_ERROR summary", () => {
	const completeEvent = createRunErrorEvent();
	const invalidEvents = [
		withoutField(completeEvent, "steps_taken"),
		withoutField(completeEvent, "metrics"),
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(
			() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
			/steps_taken 与 metrics 必须同时存在/,
		);
	}
});

test("parseAgentStreamLine strictly validates a RUN_ERROR summary", () => {
	const completeEvent = createRunErrorEvent();
	const invalidEvents = [
		{ ...completeEvent, steps_taken: -1 },
		{
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				tool_duration_ms: -1,
			},
		},
		{
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				estimated_cost_cny: "4.43e-4",
			},
		},
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(() => parseAgentStreamLine(JSON.stringify(invalidEvent)));
	}
});

test("parseAgentStreamLine parses complete RUN_FINISHED metrics", () => {
	const event = parseAgentStreamLine(JSON.stringify(createRunFinishedEvent()));

	assert.equal(event.type, "RUN_FINISHED");
	if (event.type === "RUN_FINISHED") {
		assert.deepEqual(event.metrics.model_usage, {
			input_tokens: 120,
			output_tokens: 35,
			total_tokens: 155,
			cache_hit_input_tokens: 80,
			cache_miss_input_tokens: 40,
		});
		assert.equal(event.metrics.estimated_cost_cny, "0.00044300");
		assert.equal(typeof event.metrics.estimated_cost_cny, "string");
		assert.equal(event.metrics.pricing.tier, "peak");
	}
});

test("parseAgentStreamLine accepts every explicitly nullable metric field", () => {
	const completeEvent = createRunFinishedEvent();
	const nullableNestedFields = {
		...completeEvent,
		metrics: {
			...completeEvent.metrics,
			model_usage: {
				...completeEvent.metrics.model_usage,
				cache_hit_input_tokens: null,
				cache_miss_input_tokens: null,
			},
			model_duration_ms: null,
			estimated_cost_cny: null,
		},
	};
	const nullableUsage = {
		...completeEvent,
		metrics: {
			...completeEvent.metrics,
			model_usage: null,
		},
	};

	const eventWithNullableFields = parseAgentStreamLine(
		JSON.stringify(nullableNestedFields),
	);
	const eventWithoutUsage = parseAgentStreamLine(JSON.stringify(nullableUsage));

	assert.equal(eventWithNullableFields.type, "RUN_FINISHED");
	if (eventWithNullableFields.type === "RUN_FINISHED") {
		assert.deepEqual(eventWithNullableFields.metrics.model_usage, {
			input_tokens: 120,
			output_tokens: 35,
			total_tokens: 155,
			cache_hit_input_tokens: null,
			cache_miss_input_tokens: null,
		});
		assert.equal(eventWithNullableFields.metrics.model_duration_ms, null);
		assert.equal(eventWithNullableFields.metrics.estimated_cost_cny, null);
	}

	assert.equal(eventWithoutUsage.type, "RUN_FINISHED");
	if (eventWithoutUsage.type === "RUN_FINISHED") {
		assert.equal(eventWithoutUsage.metrics.model_usage, null);
	}
});

test("parseAgentStreamLine rejects missing RUN_FINISHED fields", () => {
	const completeEvent = createRunFinishedEvent();
	const metricFields = [
		"model_usage",
		"model_duration_ms",
		"tool_duration_ms",
		"estimated_cost_cny",
		"pricing",
	];
	const usageFields = [
		"input_tokens",
		"output_tokens",
		"total_tokens",
		"cache_hit_input_tokens",
		"cache_miss_input_tokens",
	];
	const pricingFields = [
		"model",
		"tier",
		"cache_hit_input_cny_per_million",
		"cache_miss_input_cny_per_million",
		"output_cny_per_million",
	];
	const invalidEvents = [
		withoutField(completeEvent, "steps_taken"),
		withoutField(completeEvent, "metrics"),
		...metricFields.map((field) => ({
			...completeEvent,
			metrics: withoutField(completeEvent.metrics, field),
		})),
		...usageFields.map((field) => ({
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				model_usage: withoutField(completeEvent.metrics.model_usage, field),
			},
		})),
		...pricingFields.map((field) => ({
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				pricing: withoutField(completeEvent.metrics.pricing, field),
			},
		})),
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(() => parseAgentStreamLine(JSON.stringify(invalidEvent)));
	}
});

test("parseAgentStreamLine rejects negative Token counts", () => {
	const completeEvent = createRunFinishedEvent();
	const invalidEvent = {
		...completeEvent,
		metrics: {
			...completeEvent.metrics,
			model_usage: {
				...completeEvent.metrics.model_usage,
				input_tokens: -1,
			},
		},
	};

	assert.throws(
		() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
		/非负整数：input_tokens/,
	);
});

test("parseAgentStreamLine rejects an unsupported pricing tier", () => {
	const completeEvent = createRunFinishedEvent();
	const invalidEvent = {
		...completeEvent,
		metrics: {
			...completeEvent.metrics,
			pricing: {
				...completeEvent.metrics.pricing,
				tier: "weekend",
			},
		},
	};

	assert.throws(
		() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
		/字段值错误：tier/,
	);
});

test("parseAgentStreamLine rejects invalid nested object structures", () => {
	const completeEvent = createRunFinishedEvent();
	const invalidEvents = [
		{ ...completeEvent, metrics: [] },
		{
			...completeEvent,
			metrics: { ...completeEvent.metrics, model_usage: [] },
		},
		{
			...completeEvent,
			metrics: { ...completeEvent.metrics, pricing: null },
		},
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(() => parseAgentStreamLine(JSON.stringify(invalidEvent)));
	}
});

test("parseAgentStreamLine rejects invalid money strings", () => {
	const completeEvent = createRunFinishedEvent();
	const invalidEvents = [
		{
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				estimated_cost_cny: 0.000443,
			},
		},
		{
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				estimated_cost_cny: "4.43e-4",
			},
		},
		{
			...completeEvent,
			metrics: {
				...completeEvent.metrics,
				pricing: {
					...completeEvent.metrics.pricing,
					output_cny_per_million: "-9.0",
				},
			},
		},
	];

	for (const invalidEvent of invalidEvents) {
		assert.throws(
			() => parseAgentStreamLine(JSON.stringify(invalidEvent)),
			/非负十进制字符串/,
		);
	}
});

test("parseAgentStreamLine rejects unknown or incomplete events", () => {
	assert.throws(
		() => parseAgentStreamLine('{"type":"UNKNOWN"}'),
		/无法识别的 Agent 事件/,
	);
	assert.throws(
		() => parseAgentStreamLine('{"type":"TEXT_MESSAGE_CONTENT"}'),
		/缺少字符串字段：chunk/,
	);
	assert.throws(
		() => parseAgentStreamLine("not-json"),
		/无效的 Agent 事件/,
	);
});
