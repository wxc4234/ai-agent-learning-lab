import assert from "node:assert/strict";
import { test } from "node:test";
import { parseFileEditProposal } from "../../../src/features/chat/file-edit-proposal-view.ts";
import { renderToolResult } from "./render-tool-result.ts";
const base = { proposal_id: "a".repeat(32), status: "pending", relative_path: "src/中文.txt", baseline_sha256: "b".repeat(64), proposed_sha256: "c".repeat(64), created_at: "2026-09-21T12:34:56.123456+00:00", diff_truncated: false };
const parse = (patch = {}) => parseFileEditProposal("create_file_edit_proposal", JSON.stringify({ ...base, ...patch }));
for (const [field, values] of Object.entries({
    proposal_id: [null, "a".repeat(31), "A".repeat(32), "a".repeat(31) + "\n"],
    status: [undefined, "approved", "applied"], relative_path: [null, "", "😀".repeat(4097)],
    baseline_sha256: [null, "b".repeat(63), "B".repeat(64)], proposed_sha256: [null, "g".repeat(64), "c".repeat(63) + "\n"],
    created_at: [null, "yesterday", "2026-09-21T12:34:56", "2026-02-30T12:34:56Z", "2026-02-29T12:34:56Z", "2026-13-01T12:34:56Z", "2026-09-21T24:34:56Z", "2026-09-21T12:60:56Z", "2026-09-21T12:34:60Z", "2026-09-21T12:34:56+24:00"],
    diff_truncated: [undefined, 0, "false"],
})) values.forEach((value, index) => test(`invalid ${field} ${index}`, () => assert.equal(parse({ [field]: value }), null)));
test("identity, malformed data and missing fields", () => {
    assert.equal(parseFileEditProposal("preview_file_edit", JSON.stringify(base)), null);
    for (const raw of ["bad", "null", "[]", "true", "42"]) assert.equal(parseFileEditProposal("create_file_edit_proposal", raw), null);
    for (const key of Object.keys(base)) assert.equal(parse({ [key]: undefined }), null);
});
test("valid time, Unicode limits and private fields", () => {
    for (const created_at of [base.created_at, "2024-02-29T12:34:56Z", "2026-09-21T12:34:56+08:00"]) assert.ok(parse({ created_at }));
    const result = parse({ relative_path: "😀".repeat(4096), bound_root: "PRIVATE", proposed_content: "PRIVATE", diff: "PRIVATE" });
    assert.ok(result); assert.equal(Object.keys(result).length, 6); assert.ok(!JSON.stringify(result).includes("PRIVATE"));
});
for (const truncated of [true, false]) test(`render receipt ${truncated}`, () => {
    const html = renderToolResult("create_file_edit_proposal", JSON.stringify({ ...base, diff_truncated: truncated }));
    for (const text of ["提案已保存，待审批", "尚未写入文件", base.relative_path, base.proposal_id, base.created_at, base.baseline_sha256, base.proposed_sha256, "不代表当前审批状态"]) assert.ok(html.includes(text), text);
    assert.ok(html.includes(truncated ? "Diff 已截断，审阅内容不完整" : "保存的 Diff 未截断"));
    assert.ok(!html.includes("<button") && !html.includes(" open="));
    assert.ok(html.includes(`<time dateTime="${base.created_at}"`));
});
test("HTML escaping and fallback", () => {
    const html = renderToolResult("create_file_edit_proposal", JSON.stringify({ ...base, relative_path: "<script>x</script>" }));
    assert.ok(html.includes("&lt;script&gt;") && !html.includes("<script>"));
    const fallback = renderToolResult("create_file_edit_proposal", "<img src=x>");
    assert.ok(fallback.includes("提案回执格式未识别") && fallback.includes("&lt;img"));
    assert.ok(!fallback.includes("提案已保存，待审批"));
});
