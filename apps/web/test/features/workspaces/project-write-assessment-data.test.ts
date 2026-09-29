import assert from "node:assert/strict";
import { test } from "node:test";
import { readAssessmentResult, assessmentLabels } from "../../../src/features/workbench/project-write-assessment-data.ts";

const workspace = "a".repeat(32), task = "b".repeat(32), proposal = "c".repeat(32);
const receipt = { workspace_id: workspace, task_id: task, proposal_id: proposal, result: "grant_revoked" };
const parse = (raw: unknown) => readAssessmentResult(raw, workspace, task, proposal);
for (const result of [
    "invalid_facts", "not_authorized", "apply_not_requested", "grant_missing", "grant_revoked",
    "grant_changed", "target_changed", "proposal_not_approved", "application_not_idle", "diff_incomplete",
    "baseline_changed", "candidate_changed", "filesystem_unconfirmed", "platform_unsupported", "exclusive_access_unconfirmed", "eligible",
]) {
    test(`public result ${result}`, () => {
        assert.equal(parse({ ...receipt, result, private: "PRIVATE" }), result);
        assert.equal(Object.keys(assessmentLabels).length, 16);
    });
}
for (const [label, raw] of [
    ["null", null], ["array", []], ["empty", {}],
    ["unknown", { ...receipt, result: "PRIVATE" }], ["prototype", { ...receipt, result: "toString" }],
    ["non-string", { ...receipt, result: true }], ["no-result", { ...receipt, result: undefined }],
] as const) {
    test(`reject ${label}`, () => assert.equal(parse(raw), null));
}
for (const key of ["workspace_id", "task_id", "proposal_id"]) {
    test(`reject mismatch and missing ${key}`, () => {
        assert.equal(parse({ ...receipt, [key]: "d".repeat(32) }), null);
        assert.equal(parse({ ...receipt, [key]: undefined }), null);
    });
}
test("reject invalid resource context even if receipt matches", () => {
    assert.equal(readAssessmentResult({ ...receipt, workspace_id: "bad" }, "bad", task, proposal), null);
});
