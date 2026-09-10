import assert from "node:assert/strict";
import { test } from "node:test";

import { formatToolDuration } from "../../../src/features/chat/tool-duration-view.ts";

test("formatToolDuration hides duration while a tool is running", () => {
	assert.equal(formatToolDuration(undefined), null);
});

test("formatToolDuration explains a pre-execution tool error", () => {
	assert.equal(formatToolDuration(null), "未进入执行阶段");
});

test("formatToolDuration preserves a real zero duration", () => {
	assert.equal(formatToolDuration(0), "0 ms");
});

test("formatToolDuration formats a measured tool duration", () => {
	assert.equal(formatToolDuration(12), "12 ms");
});
