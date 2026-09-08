import assert from "node:assert/strict";
import { test } from "node:test";

import {
  initialChatState,
  chatReducer,
  toUserFacingError,
  type ChatState,
} from "./chat-state.ts";

test("a submitted prompt moves from thinking to streaming and then done", () => {
  let state: ChatState = initialChatState;

  state = chatReducer(state, { type: "submit", prompt: "你好" });
  assert.equal(state.status, "thinking");
  assert.equal(state.lastPrompt, "你好");

  state = chatReducer(state, { type: "stream-start" });
  state = chatReducer(state, { type: "append", chunk: "你好，" });
  state = chatReducer(state, { type: "append", chunk: "有什么可以帮你？" });
  state = chatReducer(state, { type: "complete" });

  assert.equal(state.status, "done");
  assert.equal(state.reply, "你好，有什么可以帮你？");
  assert.equal(state.errorMessage, null);
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

  state = chatReducer(state, {
    type: "tool-result",
    toolCallId: "call-area",
    result: "12",
  });
  assert.equal(state.tools[0]?.status, "succeeded");
  assert.equal(state.tools[0]?.result, "12");

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
  });

  assert.equal(state.tools[1]?.status, "failed");
  assert.equal(state.tools[1]?.errorMessage, "工具未注册");
  assert.equal(state.status, "thinking");
  assert.equal(state.errorMessage, null);
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

  state = chatReducer(state, { type: "retry" });

  assert.equal(state.status, "thinking");
  assert.equal(state.reply, "");
  assert.equal(state.errorMessage, null);
  assert.equal(state.lastPrompt, "重试这次请求");
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
  assert.notEqual(state.status, "aborted");
});
