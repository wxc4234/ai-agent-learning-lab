// 实时与历史共用严格协议检查；由退出与报告事实复算结论，拒绝矛盾的passed。
const countKeys = ["tests_run", "successful_tests", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"] as const;
type Counts = Record<typeof countKeys[number], number>;
export type VerificationView = {
    outcome: "passed" | "failed" | "unconfirmed";
    command: {
        status: "exited" | "timed_out" | "cancelled" | "start_failed";
        exit_code: number | null;
        oom_killed: boolean | null;
        daemon_error: boolean | null;
        stdout_truncated: boolean;
        stderr_truncated: boolean;
        duration_ms: number;
    };
    counts: Counts | null;
};

function object(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}
function integer(value: unknown, max = Number.MAX_SAFE_INTEGER): value is number {
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
}
function fact(value: unknown): value is boolean | null {
    return value === null || typeof value === "boolean";
}
function keys(value: Record<string, unknown>, expected: readonly string[]): boolean {
    return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}

export function parseVerification(toolName: string, raw: string): VerificationView | null {
    if (toolName !== "verify_task_sample" || raw.length > 4096 || new TextEncoder().encode(raw).length > 4096) return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (!object(value) || !keys(value, ["source", "scope", "plan_id", "outcome", "command", "report_status", "counts"])
            || value.source !== "trusted_sample_snapshot" || value.scope !== "controlled_sample_only"
            || value.plan_id !== "sample_unittest_v1") return null;
        const c = value.command;
        if (!object(c) || !keys(c, ["status", "exit_code", "oom_killed", "daemon_error", "stdout_truncated", "stderr_truncated", "duration_ms"])
            || typeof c.status !== "string" || !["exited", "timed_out", "cancelled", "start_failed"].includes(c.status)
            || (c.exit_code !== null && !integer(c.exit_code, 255))
            || (c.status === "exited" && c.exit_code === null)
            || !fact(c.oom_killed) || !fact(c.daemon_error)
            || typeof c.stdout_truncated !== "boolean" || typeof c.stderr_truncated !== "boolean"
            || !integer(c.duration_ms)) return null;
        if (c.status === "start_failed" && (c.exit_code !== null || c.oom_killed !== null || c.daemon_error !== null
            || c.stdout_truncated || c.stderr_truncated)) return null;
        let counts: Counts | null = null;
        if (value.report_status === "complete") {
            const r = value.counts;
            if (c.status !== "exited" || c.stdout_truncated || !object(r) || !keys(r, countKeys)
                || !countKeys.every(key => integer(r[key], 10000))) return null;
            counts = Object.fromEntries(countKeys.map(key => [key, r[key]])) as Counts;
            if (counts.successful_tests + counts.expected_failures + counts.unexpected_successes > counts.tests_run) return null;
            if (c.exit_code === 0 && (counts.failures || counts.errors || counts.unexpected_successes)) return null;
        } else if (value.report_status !== "unavailable" || value.counts !== null) return null;
        const outcome = c.status !== "exited" ? "unconfirmed"
            : c.exit_code !== 0 || c.oom_killed === true || c.daemon_error === true ? "failed"
            : c.oom_killed !== false || c.daemon_error !== false || c.stdout_truncated || c.stderr_truncated
                || counts === null || counts.successful_tests === 0 ? "unconfirmed" : "passed";
        if (value.outcome !== outcome) return null;
        return { outcome, command: c as VerificationView["command"], counts };
    } catch {
        return null;
    }
}
