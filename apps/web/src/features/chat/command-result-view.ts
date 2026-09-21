// 只解释已知公开协议；无法确认的结果交回通用文本展示。
export type CommandResultView = {
    status: "exited" | "timed_out" | "cancelled" | "start_failed";
    exit_code: number | null;
    oom_killed: boolean | null;
    daemon_error: boolean | null;
    stdout: string;
    stderr: string;
    stdout_truncated: boolean;
    stderr_truncated: boolean;
    duration_ms: number;
    start_error_code: string | null;
};

const startErrors: Record<string, string> = {
    working_directory_unavailable: "工作目录不可用",
    executable_unavailable: "程序不可用",
    permission_denied: "权限不足",
    sandbox_unavailable: "沙箱不可用",
    process_start_failed: "进程启动失败",
};

export function parseCommandResult(toolName: string, raw: string): CommandResultView | null {
    if (toolName !== "run_command") return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        const v = value as Record<string, unknown>;
        if (typeof v.status !== "string" || !["exited", "timed_out", "cancelled", "start_failed"].includes(String(v.status))) return null;
        if (v.exit_code !== null && !Number.isSafeInteger(v.exit_code)) return null;
        if (!Number.isSafeInteger(v.duration_ms) || (v.duration_ms as number) < 0) return null;
        for (const key of ["oom_killed", "daemon_error"]) {
            // 旧结果缺少错误事实时保留未知，不能推断命令成功。
            if (v[key] !== undefined && v[key] !== null && typeof v[key] !== "boolean") return null;
        }
        for (const key of ["stdout", "stderr"]) {
            const output = v[key];
            // Python 协议按 Unicode 字符计数，不能把 emoji 的两个 UTF-16 单元算成两个字符。
            if (typeof output !== "string" || output.length > 131072 || [...output].length > 65536) return null;
            if (typeof v[`${key}_truncated`] !== "boolean") return null;
        }
        const error = v.start_error_code ?? null;
        if (v.status === "start_failed") {
            if (typeof error !== "string" || !Object.hasOwn(startErrors, error)
                || v.exit_code !== null || v.oom_killed != null || v.daemon_error != null
                || v.stdout !== "" || v.stderr !== "" || v.stdout_truncated || v.stderr_truncated) return null;
        } else if (error !== null || (v.status === "exited" && v.exit_code === null)) return null;
        // 仅复制展示字段；不把协议扩展或私有字段传播到组件。
        return {
            status: v.status as CommandResultView["status"],
            exit_code: v.exit_code as number | null,
            oom_killed: (v.oom_killed ?? null) as boolean | null,
            daemon_error: (v.daemon_error ?? null) as boolean | null,
            stdout: v.stdout as string,
            stderr: v.stderr as string,
            stdout_truncated: v.stdout_truncated as boolean,
            stderr_truncated: v.stderr_truncated as boolean,
            duration_ms: v.duration_ms as number,
            start_error_code: error as string | null,
        };
    } catch {
        return null;
    }
}

export function commandStatusLabel(result: CommandResultView): string {
    if (result.status === "start_failed") return startErrors[result.start_error_code!] ?? "启动失败";
    if (result.status === "timed_out") return "命令超时";
    if (result.status === "cancelled") return "命令已取消";
    if (result.exit_code !== 0 || result.oom_killed === true || result.daemon_error === true) return "命令失败";
    if (result.oom_killed === null || result.daemon_error === null) return "已退出，成功未确认";
    return "命令成功";
}
