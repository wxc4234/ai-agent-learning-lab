import assert from "node:assert/strict";
import { test } from "node:test";

import {
  initialChatState,
  chatReducer,
  toUserFacingError,
  type ChatState,
} from "../../../src/features/chat/chat-state.ts";
import type { AgentRunMetrics } from "../../../src/features/chat/agent-stream.ts";

const completedRunMetrics: AgentRunMetrics = {
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
};

test("a submitted prompt moves from thinking to streaming and then done", () => {
  let state: ChatState = initialChatState;

  state = chatReducer(state, { type: "submit", prompt: "你好" });
  assert.equal(state.status, "thinking");
  assert.equal(state.lastPrompt, "你好");
  assert.equal(state.runSummary, null);

  state = chatReducer(state, { type: "stream-start" });
  state = chatReducer(state, { type: "append", chunk: "你好，" });
  state = chatReducer(state, { type: "append", chunk: "有什么可以帮你？" });
  state = chatReducer(state, {
    type: "complete",
    stepsTaken: 2,
    metrics: completedRunMetrics,
  });

  assert.equal(state.status, "done");
  assert.equal(state.reply, "你好，有什么可以帮你？");
  assert.equal(state.errorMessage, null);
  assert.deepEqual(state.runSummary, {
    stepsTaken: 2,
    metrics: completedRunMetrics,
  });
});

test("a new submission or retry clears the previous run summary", () => {
  let state = chatReducer(initialChatState, {
    type: "submit",
    prompt: "第一次请求",
  });
  state = chatReducer(state, {
    type: "complete",
    stepsTaken: 1,
    metrics: completedRunMetrics,
  });

  const submittedState = chatReducer(state, {
    type: "submit",
    prompt: "第二次请求",
  });
  const retriedState = chatReducer(state, { type: "retry" });

  assert.equal(submittedState.status, "thinking");
  assert.equal(submittedState.runSummary, null);
  assert.equal(retriedState.status, "thinking");
  assert.equal(retriedState.runSummary, null);
});

test("tool events update activity without failing the whole run", () => {
  let state = chatReducer(initialChatState, {
    type: "submit",
    prompt: "计算矩形面积",
  });

  state = chatReducer(state, {
    type: "tool-start",
    toolCallId: "call-area",
    toolName: "calculate_rectangle_area",
    arguments: '{"width": 3, "height": 4}',
  });
  assert.deepEqual(state.tools[0], {
    toolCallId: "call-area",
    toolName: "calculate_rectangle_area",
    arguments: '{"width": 3, "height": 4}',
    status: "running",
  });
	assert.equal(Object.hasOwn(state.tools[0] ?? {}, "durationMs"), false);

  state = chatReducer(state, {
    type: "tool-result",
    toolCallId: "call-area",
    result: "12",
		durationMs: 12,
  });
  assert.equal(state.tools[0]?.status, "succeeded");
  assert.equal(state.tools[0]?.result, "12");
	assert.equal(state.tools[0]?.durationMs, 12);

  state = chatReducer(state, {
    type: "tool-start",
    toolCallId: "call-missing",
    toolName: "unknown_tool",
    arguments: "{}",
  });
  state = chatReducer(state, {
    type: "tool-error",
    toolCallId: "call-missing",
    message: "工具未注册",
		durationMs: null,
  });

  assert.equal(state.tools[1]?.status, "failed");
  assert.equal(state.tools[1]?.errorMessage, "工具未注册");
	assert.equal(state.tools[1]?.durationMs, null);
  assert.equal(state.status, "thinking");
  assert.equal(state.errorMessage, null);
});

test("an execution tool error keeps its measured duration", () => {
	let state = chatReducer(initialChatState, {
		type: "tool-start",
		toolCallId: "call-failed",
		toolName: "unstable_tool",
		arguments: "{}",
	});

	state = chatReducer(state, {
		type: "tool-error",
		toolCallId: "call-failed",
		message: "工具执行失败",
		durationMs: 7,
	});

	assert.equal(state.tools[0]?.status, "failed");
	assert.equal(state.tools[0]?.durationMs, 7);
});

test("a new submission or retry removes completed tool durations", () => {
	let state = chatReducer(initialChatState, {
		type: "tool-start",
		toolCallId: "call-area",
		toolName: "calculate_rectangle_area",
		arguments: '{"width": 3, "height": 4}',
	});
	state = chatReducer(state, {
		type: "tool-result",
		toolCallId: "call-area",
		result: "12",
		durationMs: 12,
	});

	assert.equal(state.tools[0]?.durationMs, 12);

	const submittedState = chatReducer(state, {
		type: "submit",
		prompt: "新的问题",
	});
	const retriedState = chatReducer(state, { type: "retry" });

	assert.deepEqual(submittedState.tools, []);
	assert.deepEqual(retriedState.tools, []);
});

test("an aborted stream keeps partial output and enters aborted", () => {
  let state = chatReducer(initialChatState, {
    type: "submit",
    prompt: "写一段长回答",
  });
  state = chatReducer(state, { type: "stream-start" });
  state = chatReducer(state, { type: "append", chunk: "已经生成的内容" });

  state = chatReducer(state, { type: "abort" });

  assert.equal(state.status, "aborted");
  assert.equal(state.reply, "已经生成的内容");
  assert.equal(state.errorMessage, null);
  assert.equal(state.runSummary, null);
});

test("an error can be retried without duplicating the previous reply", () => {
  let state = chatReducer(initialChatState, {
    type: "submit",
    prompt: "重试这次请求",
  });
  state = chatReducer(state, { type: "stream-start" });
  state = chatReducer(state, { type: "append", chunk: "半截" });
  state = chatReducer(state, {
    type: "fail",
    message: "网络连接中断，请重试。",
  });

  assert.equal(state.status, "error");
  assert.equal(state.errorMessage, "网络连接中断，请重试。");
  assert.equal(state.runSummary, null);

  state = chatReducer(state, { type: "retry" });

  assert.equal(state.status, "thinking");
  assert.equal(state.reply, "");
  assert.equal(state.errorMessage, null);
  assert.equal(state.lastPrompt, "重试这次请求");
  assert.equal(state.runSummary, null);
});

test("server failures are converted into readable retry guidance", () => {
  assert.equal(
    toUserFacingError(new Response("", { status: 429 })),
    "请求太频繁了，请稍后再试。",
  );
  assert.equal(
    toUserFacingError(new Response("", { status: 502 })),
    "模型服务暂时不可用，请稍后重试。",
  );
  assert.equal(
    toUserFacingError(new Response("", { status: 500 })),
    "服务暂时出错，请稍后重试。",
  );
});

test("a timeout becomes an error rather than an aborted run", () => {
  let state = chatReducer(initialChatState, {
    type: "submit",
    prompt: "写一段较长的回答",
  });

  state = chatReducer(state, {type: "stream-start"});
  state = chatReducer(state, {type: "append", chunk: "已生成的部分内容"});

  state = chatReducer(state, {
    type: "fail",
    message: "请求超时了，请稍后重试。",
  });

  assert.equal(state.status, "error");
  assert.equal(state.reply, "已生成的部分内容");
  assert.equal(state.errorMessage, "请求超时了，请稍后重试。");
  assert.equal(state.runSummary, null);
  assert.notEqual(state.status, "aborted");
});
