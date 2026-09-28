import assert from "node:assert/strict";
import test from "node:test";
import { parseTaskSampleDiff, SAMPLE_BASELINE_SHA256 } from "../../../src/features/chat/task-sample-diff-view.ts";
const base = { source: "task_application_sample", status: "complete", comparison: "fixed_old_to_snapshot", path: "example.txt", baseline_sha256: SAMPLE_BASELINE_SHA256, content_sha256: SAMPLE_BASELINE_SHA256, format: "git_diff", encoding: "utf-8", byte_count: 0, diff: "" };
const parse = (value: unknown) => parseTaskSampleDiff("read_task_sample_diff", JSON.stringify(value));
test("空结果及原始Unicode/HTML/BOM文本", () => {
    assert.equal(parse(base)?.diff, "");
    const diff = '\ufeff+你好<img src=x onerror="alert(1)">\n';
    assert.equal(parse({ ...base, diff, byte_count: Buffer.byteLength(diff) })?.diff, diff);
});
for (const change of [{ source: "task_git_sample" }, { comparison: "index_to_worktree" }, { status: "partial" }, { path: "other" }, { baseline_sha256: "0".repeat(64) }, { content_sha256: "Z".repeat(64) }, { format: "html" }, { encoding: "ascii" }, { byte_count: 1 }, { byte_count: -1 }, { byte_count: 0.5 }, { private: "/secret" }, { diff: "\ud800", byte_count: 3 }]) {
    test(`拒绝非法协议 ${JSON.stringify(change)}`, () => assert.equal(parse({ ...base, ...change }), null));
}
test("JSON结构与工具名", () => {
    for (const raw of ["null", "[]", "{", "{}", JSON.stringify({ ...base, diff: null })]) assert.equal(parseTaskSampleDiff("read_task_sample_diff", raw), null);
    assert.equal(parseTaskSampleDiff("git_sample_diff", JSON.stringify(base)), null);
});
test("原始与公开字节预算", () => {
    const diff = "x".repeat(256 * 1024);
    assert.ok(parse({ ...base, diff, byte_count: diff.length }));
    assert.equal(parse({ ...base, diff: diff + "x", byte_count: diff.length + 1 }), null);
    const raw = JSON.stringify(base);
    assert.ok(parseTaskSampleDiff("read_task_sample_diff", raw + " ".repeat(1024 * 1024 - raw.length)));
    assert.equal(parseTaskSampleDiff("read_task_sample_diff", raw + " ".repeat(1024 * 1024 - raw.length + 1)), null);
    assert.equal(parse({ ...base, diff: "中".repeat(100000), byte_count: 300000 }), null);
});
