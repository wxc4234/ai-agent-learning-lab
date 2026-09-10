// 本课新增：只负责渲染正常完成后的运行指标。
import type { CompletedRunSummary } from "../chat-state";
import { createRunSummaryMetrics } from "../run-summary-view";

type RunSummaryCardProps = {
	summary: CompletedRunSummary;
};

export default function RunSummaryCard({
	summary,
}: RunSummaryCardProps) {
	const metrics = createRunSummaryMetrics(summary);

	return (
		<section
			aria-label="本次运行指标"
			className="mt-4 rounded-xl border border-zinc-200 bg-white p-4"
		>
			<h2 className="text-sm font-medium text-zinc-700">
				运行摘要
			</h2>

			<dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
				{metrics.map((metric) => (
					<div
						className="rounded-lg bg-zinc-50 px-3 py-2"
						key={metric.key}
					>
						<dt className="text-xs text-zinc-500">
							{metric.label}
						</dt>
						<dd className="mt-1 break-all text-sm font-semibold text-zinc-900">
							{metric.value}
						</dd>
					</div>
				))}
			</dl>
		</section>
	);
}
