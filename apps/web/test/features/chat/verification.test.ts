import assert from "node:assert/strict";
import test from "node:test";
import { parseVerification } from "../../../src/features/chat/verification-view.ts";

const counts = { tests_run: 1, successful_tests: 1, failures: 0, errors: 0, skipped: 0, expected_failures: 0, unexpected_successes: 0 };
const command = { status: "exited", exit_code: 0, oom_killed: false, daemon_error: false, stdout_truncated: false, stderr_truncated: false, duration_ms: 12 };
const base = { source: "trusted_sample_snapshot", scope: "controlled_sample_only", plan_id: "sample_unittest_v1", outcome: "passed", command, report_status: "complete", counts };
const parse = (v: unknown) => parseVerification("verify_task_sample", JSON.stringify(v));

test("通过、失败、零测试、全跳过与缺报告保持不同", () => {
    assert.equal(parse(base)?.outcome, "passed");
    assert.equal(parse({ ...base, outcome: "failed", command: { ...command, exit_code: 1 }, counts: { ...counts, successful_tests: 0, failures: 1 } })?.outcome, "failed");
    for (const changes of [{ tests_run: 0 }, { skipped: 1 }, { expected_failures: 1 }]) {
        assert.equal(parse({ ...base, outcome: "unconfirmed", counts: { ...counts, successful_tests: 0, ...changes } })?.outcome, "unconfirmed");
    }
    assert.equal(parse({ ...base, outcome: "unconfirmed", counts: null, report_status: "unavailable" })?.counts, null);
});
for (const changes of [{ stderr_truncated: true }, { oom_killed: null }, { daemon_error: null }]) {
    test(`未知事实不能冒充通过 ${JSON.stringify(changes)}`, () => {
        assert.equal(parse({ ...base, command: { ...command, ...changes } }), null);
        assert.equal(parse({ ...base, outcome: "unconfirmed", command: { ...command, ...changes } })?.outcome, "unconfirmed");
    });
}
for (const status of ["cancelled", "timed_out"]) {
    test(`${status}即使exit0也未确认`, () => {
        const item = { ...base, outcome: "unconfirmed", command: { ...command, status }, report_status: "unavailable", counts: null };
        assert.equal(parse(item)?.outcome, "unconfirmed");
        assert.equal(parse({ ...item, outcome: "passed" }), null);
    });
}
for (const changes of [{ tests_run: -1 }, { successful_tests: 2 }, { errors: 1 }, { failures: 1 }, { unexpected_successes: 1 }, { skipped: 0.5 }, { tests_run: "1" }, { errors: 10001 }]) {
    test(`拒绝计数或退出矛盾 ${JSON.stringify(changes)}`, () => assert.equal(parse({ ...base, counts: { ...counts, ...changes } }), null));
}
test("fixture错误和subTest事件不误作方法计数", () => {
    assert.ok(parse({ ...base, outcome: "failed", command: { ...command, exit_code: 1 }, counts: { ...counts, tests_run: 0, successful_tests: 0, errors: 1 } }));
    assert.ok(parse({ ...base, outcome: "unconfirmed", counts: { ...counts, successful_tests: 0, skipped: 3 } }));
});
test("报告截断必须不可用，计数null与0不混淆", () => {
    assert.equal(parse({ ...base, command: { ...command, stdout_truncated: true } }), null);
    assert.ok(parse({ ...base, outcome: "unconfirmed", command: { ...command, stdout_truncated: true }, report_status: "unavailable", counts: null }));
    assert.equal(parse({ ...base, report_status: "unavailable" }), null);
});
test("拒绝非法来源、未知计划、额外字段和不合法JSON", () => {
    for (const changes of [{ source: "project" }, { scope: "all" }, { plan_id: "pytest" }, { outcome: "success" }, { counts: [] }, { raw: "<script>" }, { command: { ...command, duration_ms: -1 } }]) assert.equal(parse({ ...base, ...changes }), null);
    for (const raw of ["{", "null", "[]", "{}", "x".repeat(4097)]) assert.equal(parseVerification("verify_task_sample", raw), null);
    assert.equal(parseVerification("other", JSON.stringify(base)), null);
});
test("UTF8字节预算与边界", () => {
    const raw = JSON.stringify(base);
    assert.ok(parseVerification("verify_task_sample", raw + " ".repeat(4096 - raw.length)));
    assert.equal(parseVerification("verify_task_sample", raw + " ".repeat(4097 - raw.length)), null);
    assert.equal(parse({ ...base, private: "中".repeat(1400) }), null);
});

test("进程状态必须是字符串枚举，不能通过数组转换混入", () => {
    assert.equal(parse({ ...base, outcome: "unconfirmed", command: { ...command, status: ["exited"] }, report_status: "unavailable", counts: null }), null);
});
