import type { CompletedRunSummary } from "../chat-state";
import { createRunSummaryMetrics } from "../run-summary-view";

// 输入框外的轻量指标栏；复用真实运行数据，未知值不补成零或推算生成速度。
export default function RunMetricsFooter({
    summary,
    failed,
    running = false,
}: {
    summary: CompletedRunSummary | null;
    failed: boolean;
    running?: boolean;
}) {
    // 栏位始终存在；未完成/刷新后没有本轮摘要时，不拿旧数据或零填充。
    const metrics = summary ? createRunSummaryMetrics(summary) : [
        { key: 'steps', label: '步骤数', value: '暂无数据' },
        { key: 'total_tokens', label: '总 Token', value: '暂无数据' },
        { key: 'estimated_cost', label: '预估费用', value: '暂无数据' },
        { key: 'model_duration', label: '模型耗时', value: '暂无数据' },
        { key: 'tool_duration', label: '工具总耗时', value: '暂无数据' },
    ];
    // 用图标承载标签；原始数值留在提示中，不把舍入显示当作计费数据。
    const paths: Record<string, string> = {
        steps: "M4 15a8 8 0 1 1 16 0M12 13l4-5M7 19h10",
        total_tokens: "M4 6c0-4 16-4 16 0s-16 4-16 0m0 0v12c0 4 16 4 16 0V6M4 12c0 4 16 4 16 0",
        estimated_cost: "M6 4l6 7 6-7M12 11v10M5 12h14M5 16h14",
        model_duration: "M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
        tool_duration: "M8 3h8M12 3v3M16 10l-4 4M20 14a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    };
    const displayValue = (key: string, value: string) => {
        if (value === "暂无数据") return "—";
        if (key === "total_tokens" && summary?.metrics.model_usage) {
            const count = summary.metrics.model_usage.total_tokens;
            return `${count >= 1000 ? `${(count / 1000).toFixed(1)}k` : count} tok`;
        }
        if (key === "model_duration" || key === "tool_duration") {
            const duration = Number(value.replace(" ms", ""));
            return duration >= 1000 ? `${(duration / 1000).toFixed(1)} s` : value;
        }
        return value;
    };
    return (
        <section aria-label="本次运行指标" className="mx-auto mt-2 w-full max-w-[960px] px-2 text-[11px] leading-4 text-muted-foreground/80">
            <dl className="flex flex-wrap items-center justify-center gap-y-1 tabular-nums">
                {(running || failed) && (
                    <div className="flex items-center gap-1.5 px-2.5" title={failed ? "运行失败，用量以已记录数据为准" : "运行中，用量将在结束后更新"}>
                        <dt className="sr-only">运行状态</dt>
                        <dd className={failed ? "text-destructive" : ""}>
                            <span aria-hidden="true" className={`mr-1 inline-block size-1.5 rounded-full ${failed ? "bg-destructive" : "animate-pulse bg-current"}`} />
                            {failed ? "运行失败" : "运行中"}
                        </dd>
                    </div>
                )}
                {metrics.map((metric, index) => (
                    <div key={metric.key} tabIndex={0}
                        title={`${metric.label}：${metric.value}`}
                        aria-label={`${metric.label}：${metric.value}`}
                        className={`flex items-center gap-1.5 px-2.5 outline-none focus-visible:rounded focus-visible:ring-1 focus-visible:ring-ring ${index ? "border-l border-border/60" : ""}`}>
                        <dt className="flex items-center">
                            <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                <path d={paths[metric.key]} />
                            </svg>
                            <span className="sr-only">{metric.label}</span>
                        </dt>
                        <dd>{displayValue(metric.key, metric.value)}</dd>
                    </div>
                ))}
            </dl>
        </section>
    );
}
