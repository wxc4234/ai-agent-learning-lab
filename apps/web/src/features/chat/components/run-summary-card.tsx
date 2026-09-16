// 展示成功或失败运行的真实指标，文案区分运行结果
import type { CompletedRunSummary } from "../chat-state";
import { createRunSummaryMetrics } from "../run-summary-view";

type RunSummaryCardProps = {
	summary: CompletedRunSummary;
	status: "done" | "error";
};

export default function RunSummaryCard({
	summary,
	status,
}: RunSummaryCardProps) {
	const metrics = createRunSummaryMetrics(summary);
	const isFailed = status === "error";
    const title = isFailed ? "失败运行摘要" : "运行摘要";

	return (
		<section
                role="region"
			aria-label={title}
			className="border-t border-border/60 pt-4"
		>
			<h2
                className={
                    isFailed
                        ? "text-sm font-medium text-destructive"
                        : "text-sm font-medium text-foreground"
                }
            >
                {title}
            </h2>

            {isFailed && (
                <p className="mt-2 text-xs text-muted-foreground">
                    本次运行未成功完成，以下为失败前已记录的用量与耗时。
                    未知指标显示“暂无数据”。
                </p>
            )}

			<dl className="mt-3 divide-y divide-border/40">
				{metrics.map((metric) => (
					<div
						className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5"
						key={metric.key}
					>
						<dt className="text-base text-muted-foreground">
							{metric.label}
						</dt>
						<dd className="ml-auto whitespace-nowrap text-right text-base font-medium tabular-nums text-foreground">
							{metric.value}
						</dd>
					</div>
				))}
			</dl>
		</section>
	);
}
