import { renderToolResult as render } from "./render-tool-result.ts";
import assert from "node:assert/strict";
import { test } from "node:test";
import { commandStatusLabel, parseCommandResult } from "../../../src/features/chat/command-result-view.ts";

const base = { status: "exited", exit_code: 0, oom_killed: false, daemon_error: false,
    stdout: "hello", stderr: "", stdout_truncated: false, stderr_truncated: false,
    duration_ms: 0, start_error_code: null };
const parse = (patch = {}) => parseCommandResult("run_command", JSON.stringify({ ...base, ...patch }));

for (const [patch, label] of [
    [{}, "命令成功"], [{ exit_code: 7 }, "命令失败"], [{ exit_code: -9 }, "命令失败"],
    [{ oom_killed: true }, "命令失败"], [{ daemon_error: true }, "命令失败"],
    [{ oom_killed: null }, "已退出，成功未确认"], [{ daemon_error: undefined }, "已退出，成功未确认"],
    [{ status: "timed_out" }, "命令超时"], [{ status: "cancelled", exit_code: null }, "命令已取消"],
    [{ status: "start_failed", exit_code: null, oom_killed: null, daemon_error: null,
        stdout: "", start_error_code: "permission_denied" }, "权限不足"],
] as const) {
    test(`confirmed status: ${JSON.stringify(patch)}`, () => {
        const result = parse(patch);
        assert.ok(result);
        assert.equal(commandStatusLabel(result), label);
    });
}
for (const patch of [
    { status: "unknown" }, { status: ["exited"] }, { exit_code: null }, { exit_code: "0" }, { exit_code: 0.5 },
    { duration_ms: -1 }, { duration_ms: true }, { oom_killed: 0 }, { daemon_error: "false" },
    { stdout: null }, { stderr: 1 }, { stdout_truncated: "false" }, { stderr_truncated: undefined },
    { stdout: "a".repeat(65537) }, { start_error_code: "permission_denied" },
    { status: "start_failed" },
    { status: "start_failed", exit_code: null, oom_killed: null, daemon_error: null,
        stdout: "", start_error_code: "toString" },
]) {
    test(`invalid protocol falls back: ${Object.keys(patch).join()}`, () => assert.equal(parse(patch), null));
}
test("tool identity, malformed JSON and Unicode limits", () => {
    assert.equal(parseCommandResult("other", JSON.stringify(base)), null);
    for (const raw of ["oops", "null", "[]", "42"]) assert.equal(parseCommandResult("run_command", raw), null);
    assert.equal(parse({ stdout: "😀".repeat(65536) })?.stdout.length, 131072);
    assert.equal(parse({ stderr: "😀".repeat(65537) }), null);
});

// 共用真实组件渲染夹具，覆盖分派新增预览卡片后的兼容行为。
test("channels, truncation, empty output, zero duration and HTML escaping", () => {
    const html = render("run_command", JSON.stringify({ ...base, stdout: '<script>alert(1)</script>', stderr_truncated: true }));
    for (const text of ["命令成功", "0 ms", "（无输出）", "输出已截断", 'aria-label="stdout"', 'aria-label="stderr"', 'tabindex="0"', "max-h-48"]) assert.ok(html.includes(text), text);
    assert.ok(html.includes("&lt;script&gt;"));
    assert.ok(!html.includes("<script>"));
});
test("generic and invalid command outputs remain escaped text", () => {
    assert.ok(!render("other", JSON.stringify(base)).includes('aria-label="命令结果"'));
    const html = render("run_command", "<img src=x onerror=alert(1)>");
    assert.ok(html.includes("格式未识别"));
    assert.ok(html.includes("&lt;img"));
    assert.ok(!html.includes("<img"));
});
