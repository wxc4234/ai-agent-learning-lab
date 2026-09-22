import assert from "node:assert/strict";
import { test } from "node:test";
import { renderProposalActionsSnapshot } from "./render-tool-result.ts";

const detail = {
    workspace_id: "a".repeat(32), task_id: "b".repeat(32), proposal_id: "c".repeat(32),
    status: "pending" as const, relative_path: "file.txt", diff: "-old\n+new",
    diff_truncated: false, baseline_sha256: "a".repeat(64), proposed_sha256: "b".repeat(64),
    created_at: "2026-09-22T00:00:00Z",
};
for (const phase of ["checking", "submitting", "uncertain", "unavailable"]) {
    test(`${phase} disables both decisions`, () => {
        const html = renderProposalActionsSnapshot(detail, phase);
        assert.equal((html.match(/disabled=""/g) ?? []).length, 2);
        assert.ok(html.includes("不会写入文件"));
    });
}
for (const truncated of [false, true]) {
    test(`idle truncated=${truncated} permits only allowed decisions`, () => {
        const html = renderProposalActionsSnapshot({ ...detail, diff_truncated: truncated }, "idle");
        assert.equal((html.match(/disabled=""/g) ?? []).length, truncated ? 1 : 0);
        assert.ok(html.includes("拒绝提案"));
    });
}
for (const status of ["approved", "rejected"] as const) {
    test(`queried ${status} has no decision buttons`, () => {
        const html = renderProposalActionsSnapshot({ ...detail, status }, "uncertain");
        assert.ok(!html.includes("<button"));
        assert.ok(html.includes(status === "approved" ? "尚未应用到文件" : "已确认拒绝"));
    });
    test(`remembered ${status} blocks stale pending snapshot`, () => {
        assert.ok(!renderProposalActionsSnapshot(detail, status).includes("<button"));
    });
}
test("unknown result explains pending is not proof of stopped request", () => {
    assert.ok(renderProposalActionsSnapshot(detail, "uncertain").includes("不能证明旧请求已停止"));
});
