import { Card } from "@/components/ui/card";
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
		<Card
                role="region"
			aria-label={title}
			className="mt-4 gap-0 p-4 shadow-none"
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

			<dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
				{metrics.map((metric) => (
					<div
						className="rounded-lg bg-muted px-3 py-2"
						key={metric.key}
					>
						<dt className="text-xs text-muted-foreground">
							{metric.label}
						</dt>
						<dd className="mt-1 break-all text-sm font-semibold text-foreground">
							{metric.value}
						</dd>
					</div>
				))}
			</dl>
		</Card>
	);
}
