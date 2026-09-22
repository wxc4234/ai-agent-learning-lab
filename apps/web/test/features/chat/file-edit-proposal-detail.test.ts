import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToolResult, renderProposalDetailSnapshot } from "./render-tool-result.ts";

const result = JSON.stringify({ proposal_id: "c".repeat(32), status: "pending", relative_path: "file.txt",
    baseline_sha256: "a".repeat(64), proposed_sha256: "b".repeat(64), created_at: "2026-09-21T00:00:00Z", diff_truncated: false });
const scope = { workspaceId: "a".repeat(32), taskId: "b".repeat(32) };

test("no task scope keeps receipt without querying", (t) => {
    const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("must not fetch"); });
    const html = renderToolResult("create_file_edit_proposal", result);
    assert.ok(html.includes("提案已保存，待审批"));
    assert.ok(!html.includes("查看提案详情"));
    assert.equal(fetch.mock.callCount(), 0);
});
test("valid scope renders idle read button but no automatic request or Diff", (t) => {
    const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("must not fetch"); });
    const html = renderToolResult("create_file_edit_proposal", result, scope);
    assert.ok(html.includes("查看提案详情"));
    assert.ok(!html.includes('aria-label="提案 Diff"'));
    assert.ok(!html.includes("role=\"alert\""));
    assert.equal(fetch.mock.callCount(), 0);
});
for (const field of ["workspaceId", "taskId"] as const) {
    test(`invalid ${field} never enables detail`, () => {
        const html = renderToolResult("create_file_edit_proposal", result, { ...scope, [field]: "invalid" });
        assert.ok(html.includes("当前任务信息不完整"));
        assert.ok(!html.includes("查看提案详情"));
    });
}
test("unknown protocol cannot create detail controls", () => {
    const html = renderToolResult("create_file_edit_proposal", '{"status":"approved"}', scope);
    assert.ok(html.includes("提案回执格式未识别"));
    assert.ok(!html.includes("查看提案详情"));
});


for (const [status, label] of [
    ["pending", "待审批"], ["approved", "已批准"], ["rejected", "已拒绝"],
] as const) {
    test(`loaded ${status} displays current decision and escaped Diff`, () => {
        const html = renderProposalDetailSnapshot({
            proposal_id: "c".repeat(32), workspace_id: scope.workspaceId, task_id: scope.taskId,
            status, relative_path: "file.txt", baseline_sha256: "a".repeat(64),
            proposed_sha256: "b".repeat(64), created_at: "2026-09-21T00:00:00Z",
            diff_truncated: false, diff: "<script>alert(1)</script>",
        });
        assert.ok(html.includes(`查询时状态：${label}`));
        assert.ok(html.includes("&lt;script&gt;"));
        assert.ok(!html.includes("<script>"));
        assert.ok(!/>批准<|>拒绝<|>应用</.test(html));
    });
}
