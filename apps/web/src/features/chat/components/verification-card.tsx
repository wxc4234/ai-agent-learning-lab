import type { VerificationView } from "../verification-view";

const labels = { passed: "验证通过", failed: "验证失败", unconfirmed: "验证未确认" };
const states = { exited: "已退出", timed_out: "等待超时", cancelled: "已取消", start_failed: "启动失败" };
const counts = {
    tests_run: "运行测试", successful_tests: "完整通过", failures: "断言失败", errors: "错误",
    skipped: "跳过", expected_failures: "预期失败", unexpected_successes: "意外成功",
} as const;
const fact = (value: boolean | null) => value === null ? "未知" : value ? "是" : "否";

export default function VerificationCard({ value }: { value: VerificationView }) {
    return (
        <section aria-label="受控样例验证" className="mt-3 min-w-0 space-y-3 rounded-md border border-border bg-background p-3">
            <h4 className="text-sm font-medium">{labels[value.outcome]}</h4>
            <p className="text-sm">固定目标：example.txt 的内容精确等于 <code>new</code>，后接一个 LF 换行。</p>
            <p className="text-xs text-muted-foreground">仅验证本次受控样例快照，不代表普通项目测试通过。工具调用完成不等于验证通过。</p>
            <dl className="grid grid-cols-2 gap-3 text-xs">
                <div><dt className="text-muted-foreground">进程状态</dt><dd>{states[value.command.status]}</dd></div>
                <div><dt className="text-muted-foreground">退出码</dt><dd>{value.command.exit_code ?? "未取得"}</dd></div>
                <div><dt className="text-muted-foreground">耗时</dt><dd>{value.command.duration_ms} ms</dd></div>
                <div><dt className="text-muted-foreground">报告</dt><dd>{value.counts ? "完整报告" : "报告不可用"}</dd></div>
                <div><dt className="text-muted-foreground">内存超限终止</dt><dd>{fact(value.command.oom_killed)}</dd></div>
                <div><dt className="text-muted-foreground">执行服务错误</dt><dd>{fact(value.command.daemon_error)}</dd></div>
            </dl>
            {(value.command.stdout_truncated || value.command.stderr_truncated) && (
                <p className="text-sm">输出已截断，不能据此确认验证通过。</p>
            )}
            {value.counts ? (
                <dl aria-label="测试计数" className="grid grid-cols-2 gap-3 border-t border-border pt-3 text-xs">
                    {Object.entries(counts).map(([key, label]) => (
                        <div key={key}><dt className="text-muted-foreground">{label}</dt><dd>{value.counts![key as keyof typeof counts]}</dd></div>
                    ))}
                </dl>
            ) : <p className="text-sm">没有可用的完整计数，不能将其解释为零项测试通过。</p>}
            {value.counts?.successful_tests === 0 && <p className="text-sm">没有完整通过的测试方法。</p>}
            <p className="text-xs text-muted-foreground">只展示已记录结果，不会重新执行验证。</p>
        </section>
    );
}
