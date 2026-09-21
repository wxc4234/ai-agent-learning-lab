import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyDiffLine, parseFileEditPreview } from "../../../src/features/chat/file-edit-preview-view.ts";
import { renderToolResult } from "./render-tool-result.ts";

const base = { status: "preview_only", relative_path: "src/file.txt", baseline_sha256: "a".repeat(64),
    before_byte_count: 3, after_byte_count: 0, diff: '--- before\n+++ after\n@@ -1 +0 @@\n-"old"\n', diff_truncated: false };
const parse = (patch = {}) => parseFileEditPreview("preview_file_edit", JSON.stringify({ ...base, ...patch }));
for (const [field, values] of Object.entries({
    status: [undefined, "applied", true], relative_path: [null, "", "a".repeat(4097)],
    baseline_sha256: [null, "a".repeat(63), "A".repeat(64), "a".repeat(63) + "\n", "g".repeat(64)],
    before_byte_count: [null, "0", -1, 0.5, 262145], after_byte_count: [undefined, true, -1, 262145],
    diff: [null, "", "a".repeat(16385)], diff_truncated: [undefined, "false", 0],
})) {
    values.forEach((value, index) => test(`invalid ${field} case ${index}`, () => assert.equal(parse({ [field]: value }), null)));
}
test("tool identity and malformed JSON", () => {
    assert.equal(parseFileEditPreview("other", JSON.stringify(base)), null);
    for (const raw of ["oops", "null", "[]", "42"]) assert.equal(parseFileEditPreview("preview_file_edit", raw), null);
});
test("Unicode limits, zero bytes and private fields", () => {
    const result = parse({ relative_path: "😀".repeat(4096), diff: "😀".repeat(16384), after_byte_count: 0, updated_content: "PRIVATE" });
    assert.ok(result);
    assert.equal(result.afterByteCount, 0);
    assert.equal(Object.keys(result).length, 6);
    assert.ok(!JSON.stringify(result).includes("PRIVATE"));
    assert.equal(parse({ diff: "😀".repeat(16385) }), null);
    assert.equal(parse({ relative_path: "😀".repeat(4097) }), null);
    assert.ok(parse({ before_byte_count: 262144, after_byte_count: 262144 }));
});
for (const [line, kind] of [["--- before", "metadata"], ["+++ after", "metadata"], ["@@ -1 +1 @@", "metadata"],
    ['+"new"', "added"], ['-"old"', "removed"], [' "same"', "context"], ["", "context"]]) {
    test(`diff classification ${line}`, () => assert.equal(classifyDiffLine(line), kind));
}
for (const truncated of [false, true]) {
    test(`real card render truncated=${truncated}`, () => {
        const html = renderToolResult("preview_file_edit", JSON.stringify({ ...base, diff_truncated: truncated }));
        for (const text of ["仅预览，未写入", "src/file.txt", "0 字节", "3 字节", "SHA-256", "不能直接用于 git apply", 'aria-label="修改 Diff"', 'tabindex="0"', "max-h-64"]) assert.ok(html.includes(text), text);
        assert.ok(html.includes(truncated ? "Diff 已截断，审阅内容不完整" : "Diff 未截断"));
        assert.ok(!html.includes("<button"));
        assert.ok(!html.includes(" open="));
    });
}
test("HTML escaping, unchanged diff text representation and fallback", () => {
    const html = renderToolResult("preview_file_edit", JSON.stringify({ ...base, relative_path: '<img src=x>', diff: '-"<script>alert(1)</script>\\r\\n"\n+"new"\n' }));
    assert.ok(html.includes("&lt;script&gt;"));
    assert.ok(html.includes("&lt;img"));
    assert.ok(html.includes("\\r\\n"));
    assert.ok(!html.includes("<script>"));
    for (const tool of ["other", "preview_file_edit"]) {
        const fallback = renderToolResult(tool, "<img src=x>");
        assert.ok(fallback.includes('aria-label="工具结果"'));
        assert.ok(fallback.includes("&lt;img"));
        assert.equal(fallback.includes("预览结果格式未识别"), tool === "preview_file_edit");
        assert.ok(!fallback.includes('aria-label="文件修改预览"'));
    }
});
