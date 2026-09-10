// 本课新增：把完成态领域数据转换成 UI 可以直接渲染的展示数据。
import type { CompletedRunSummary } from "./chat-state";

export type RunSummaryMetric = {
	key:
		| "steps"
		| "total_tokens"
		| "estimated_cost"
		| "model_duration"
		| "tool_duration";
	label: string;
	value: string;
};

const NO_DATA_LABEL = "暂无数据";

function formatDuration(milliseconds: number | null): string {
	if (milliseconds === null) {
		return NO_DATA_LABEL;
	}

	return `${milliseconds} ms`;
}

function formatTotalTokens(summary: CompletedRunSummary): string {
	const usage = summary.metrics.model_usage;

	if (usage === null) {
		return NO_DATA_LABEL;
	}

	return `${usage.total_tokens} Token`;
}

function formatEstimatedCost(summary: CompletedRunSummary): string {
	const cost = summary.metrics.estimated_cost_cny;

	if (cost === null) {
		return NO_DATA_LABEL;
	}

	// 金额保持后端提供的十进制字符串，不转换为 JavaScript number。
	return `¥${cost}`;
}

export function createRunSummaryMetrics(
	summary: CompletedRunSummary,
): RunSummaryMetric[] {
	return [
		{
			key: "steps",
			label: "步骤数",
			value: `${summary.stepsTaken} 步`,
		},
		{
			key: "total_tokens",
			label: "总 Token",
			value: formatTotalTokens(summary),
		},
		{
			key: "estimated_cost",
			label: "预估费用",
			value: formatEstimatedCost(summary),
		},
		{
			key: "model_duration",
			label: "模型耗时",
			value: formatDuration(summary.metrics.model_duration_ms),
		},
		{
			key: "tool_duration",
			label: "工具总耗时",
			value: formatDuration(summary.metrics.tool_duration_ms),
		},
	];
}
