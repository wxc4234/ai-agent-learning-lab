import assert from "node:assert/strict";
import { test } from "node:test";

import type { CompletedRunSummary } from "../../../src/features/chat/chat-state.ts";
import { createRunSummaryMetrics } from "../../../src/features/chat/run-summary-view.ts";

const completeSummary: CompletedRunSummary = {
	stepsTaken: 2,
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

function metricValues(summary: CompletedRunSummary): Record<string, string> {
	return Object.fromEntries(
		createRunSummaryMetrics(summary).map((metric) => [
			metric.key,
			metric.value,
		]),
	);
}

test("createRunSummaryMetrics formats complete metrics", () => {
	assert.deepEqual(metricValues(completeSummary), {
		steps: "2 步",
		total_tokens: "155 Token",
		estimated_cost: "¥0.00044300",
		model_duration: "1840 ms",
		tool_duration: "12 ms",
	});
});

test("createRunSummaryMetrics shows no data for nullable missing metrics", () => {
	const summary: CompletedRunSummary = {
		...completeSummary,
		metrics: {
			...completeSummary.metrics,
			model_usage: null,
			model_duration_ms: null,
			estimated_cost_cny: null,
		},
	};

	assert.deepEqual(metricValues(summary), {
		steps: "2 步",
		total_tokens: "暂无数据",
		estimated_cost: "暂无数据",
		model_duration: "暂无数据",
		tool_duration: "12 ms",
	});
});

test("createRunSummaryMetrics preserves real zero values", () => {
	const summary: CompletedRunSummary = {
		stepsTaken: 0,
		metrics: {
			...completeSummary.metrics,
			model_usage: {
				input_tokens: 0,
				output_tokens: 0,
				total_tokens: 0,
				cache_hit_input_tokens: 0,
				cache_miss_input_tokens: 0,
			},
			model_duration_ms: 0,
			tool_duration_ms: 0,
			estimated_cost_cny: "0",
		},
	};

	assert.deepEqual(metricValues(summary), {
		steps: "0 步",
		total_tokens: "0 Token",
		estimated_cost: "¥0",
		model_duration: "0 ms",
		tool_duration: "0 ms",
	});
});
