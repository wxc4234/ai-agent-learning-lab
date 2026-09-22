import assert from "node:assert/strict";
import { test } from "node:test";
import { renderApplicationSnapshot } from "./render-tool-result.ts";

for (const [status, label] of [
    ["idle", "尚未领取执行"], ["running", "不保证执行进程仍在运行"],
    ["applied", "不保证文件后来没有被修改"], ["not_applied", "不能重新领取"],
    ["uncertain", "不自动重试或释放占用"],
]) {
    test(`application ${status} explains snapshot limits`, () => {
        const html = renderApplicationSnapshot({ phase: "ready", status });
        assert.ok(html.includes(label));
        assert.ok(html.includes("重新查询应用状态"));
        assert.ok(!/>应用<|>释放占用<|>重试执行</.test(html));
    });
}
test("idle panel does not automatically fetch", (t) => {
    const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("no fetch"); });
    assert.ok(renderApplicationSnapshot().includes("查询应用状态"));
    assert.equal(fetch.mock.callCount(), 0);
});
test("loading can be cancelled", () => {
    const html = renderApplicationSnapshot({ phase: "loading" });
    assert.ok(html.includes("取消状态查询"));
    assert.ok(html.includes("disabled"));
});
test("failure never renders idle database state", () => {
    const html = renderApplicationSnapshot({ phase: "error", message: "读取失败" });
    assert.ok(html.includes('role="alert"'));
    assert.ok(!html.includes("尚未领取执行"));
});
