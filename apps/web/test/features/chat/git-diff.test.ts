import assert from "node:assert/strict";
import test from "node:test";
import { parseGitDiff } from "../../../src/features/chat/git-diff-view.ts";

const result = {
    source: "task_git_sample", status: "complete", scope: "worktree",
    comparison: "index_to_worktree", submodules: "ignored", untracked_files: "excluded",
    format: "git_diff", encoding: "utf-8", diff: "", byte_count: 0,
};
test("两种范围与空结果", () => {
    assert.deepEqual(parseGitDiff("git_sample_diff", JSON.stringify(result)), { scope: "worktree", diff: "", byteCount: 0 });
    assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, scope: "staged", comparison: "head_to_index" }))?.scope, "staged");
});
test("保留Unicode、BOM和潜在HTML为原始文本", () => {
    const diff = "\uFEFF中文😀\n+<img src=x onerror=alert(1)>\n";
    const parsed = parseGitDiff("git_sample_diff", JSON.stringify({ ...result, diff, byte_count: new TextEncoder().encode(diff).length }));
    assert.equal(parsed?.diff, diff);
});
for (const [field, value] of Object.entries({
    source: "user_project", status: "partial", scope: "HEAD", comparison: "head_to_worktree",
    submodules: "included", untracked_files: "all", format: "patch", encoding: "base64",
    byte_count: -1, diff: null,
})) {
    test(`拒绝错误字段 ${field}`, () => {
        assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, [field]: value })), null);
    });
}
test("拒绝冒充、坏JSON、数组、缺字段和伪造字节数", () => {
    assert.equal(parseGitDiff("other", JSON.stringify(result)), null);
    for (const raw of ["{", "null", "[]", "{}", JSON.stringify({ ...result, diff: "中", byte_count: 1 }), JSON.stringify({ ...result, diff: "\ud800", byte_count: 3 })]) {
        assert.equal(parseGitDiff("git_sample_diff", raw), null);
    }
});
test("原始内容预算和公开JSON预算独立生效", () => {
    const diff = "a".repeat(256 * 1024);
    assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, diff, byte_count: diff.length }))?.diff, diff);
    assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, diff: diff + "a", byte_count: diff.length + 1 })), null);
    assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, diff: "\x01".repeat(256 * 1024), byte_count: 256 * 1024 })), null);
    assert.equal(parseGitDiff("git_sample_diff", JSON.stringify({ ...result, extra: "中".repeat(400000) })), null);
});
