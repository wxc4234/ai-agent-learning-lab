import assert from "node:assert/strict";
import { test } from "node:test";

import {
	parseAgentStreamLine,
	readAgentStream,
	type AgentStreamEvent,
} from "./agent-stream.ts";

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

test("readAgentStream handles events split across arbitrary byte chunks", async () => {
	const body = [
		JSON.stringify({
			type: "TOOL_CALL_START",
			tool_call_id: "call-area",
			tool_name: "calculate_rectangle_area",
			arguments: '{"width": 3, "height": 4}',
		}),
		JSON.stringify({
			type: "TOOL_CALL_RESULT",
			tool_call_id: "call-area",
			tool_name: "calculate_rectangle_area",
			result: "面积是 12",
		}),
		JSON.stringify({
			type: "TEXT_MESSAGE_CONTENT",
			chunk: "矩形面积是 12。",
		}),
		JSON.stringify({ type: "RUN_FINISHED", steps_taken: 2 }),
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
