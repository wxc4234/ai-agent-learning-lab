import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { readCodeBatchSummaries } from "../../../src/features/workbench/code-batch-summaries-data.ts";
import type { CodeBatchSummaries } from "../../../src/features/workbench/code-batch-summaries-data.ts";

const fixture = JSON.parse(readFileSync(new URL("./code-batch-summaries.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; results: Record<string, CodeBatchSummaries>;
};
const { workspaceId, taskId } = fixture;
function snapshot(name = "basic"): CodeBatchSummaries { return structuredClone(fixture.results[name]); }
function read(value: unknown) { return readCodeBatchSummaries(value, workspaceId, taskId); }

for (const name of Object.keys(fixture.results)) {
    test(`public Python DTO preserves ${name} and isolates nested arrays`, () => {
        const value = snapshot(name);
        const result = read(value);
        assert.deepEqual(result, value);
        assert.notEqual(result, value); assert.notEqual(result?.batches, value.batches);
        if (result?.batches.length) {
            assert.notEqual(result.batches[0], value.batches[0]);
            result.batches[0].incomplete_reasons.push("changed");
            result.batches[0].requested_model = "changed";
            assert.deepEqual(value, snapshot(name));
        }
    });
}
for (const value of [null, [], {}, "PRIVATE", 1, true]) {
    test(`unknown list shape rejects ${JSON.stringify(value)}`, () => assert.equal(read(value), null));
}
for (const changes of [
    { workspace_id: "c".repeat(32) }, { task_id: "c".repeat(32) }, { source: "PRIVATE" },
    { limit: 19 }, { limit: "20" }, { has_more: 1 }, { batches: null },
    { batches: new Array(21).fill(snapshot().batches[0]) }, { has_more: true }, { private_root: "PRIVATE" },
]) {
    test(`top-level contract rejects ${Object.keys(changes)[0]}=${JSON.stringify(changes).slice(0, 70)}`, () => {
        assert.equal(read({ ...snapshot(), ...changes }), null);
    });
}
for (const [workspace, task] of [["PRIVATE", taskId], [workspaceId, "b".repeat(32) + "\n"], ["A".repeat(32), taskId]]) {
    test(`caller scope is strict ${workspace.slice(0, 8)}/${task.slice(0, 8)}`, () => {
        assert.equal(readCodeBatchSummaries(snapshot(), workspace, task), null);
    });
}
const invalidItems: Record<string, unknown>[] = [
    { batch_id: "A".repeat(32) }, { batch_id: "a".repeat(32) + "\n" },
    { space_id: "g".repeat(64) }, { space_id: "d".repeat(63) },
    { requested_model: "" }, { requested_model: " PRIVATE " }, { requested_model: "\u0085model" },
    { response_model: "model\u001c" }, { requested_model: "PRIVATE\n" }, { response_model: "\ud800" },
    { requested_model: "x".repeat(257) }, { response_model: true },
    { dimensions: 0 }, { dimensions: 4097 }, { dimensions: 1.5 }, { dimensions: true },
    { chunk_count: 0 }, { chunk_count: 21 }, { chunk_count: "1" }, { chunk_count: Infinity },
    { truncated: true }, { truncated: "false" }, { incomplete_reasons: ["PRIVATE"] },
    { truncated: true, incomplete_reasons: ["chunk_budget", "chunk_budget"] },
    { incomplete_reasons: {} }, { incomplete_reasons: null }, { private_key: "PRIVATE" },
];
for (const [index, changes] of invalidItems.entries()) {
    test(`summary item rejects invalid field set ${index}`, () => {
        const value = snapshot(); Object.assign(value.batches[0], changes); assert.equal(read(value), null);
    });
}
for (const [name, value] of [["missing", undefined], ["null", null], ["array", []], ["primitive", true]] as const) {
    test(`invalid item shape rejects ${name}`, () => {
        const input = snapshot() as unknown as { batches: unknown[] };
        input.batches = [value]; assert.equal(read(input), null);
    });
}
for (const time of [
    "PRIVATE", "2026-10-08", "2026-10-08T00:00:00", "2026-10-08T00:00:00Z\n",
    "0000-01-01T00:00:00Z", "2026-02-29T00:00:00Z", "1900-02-29T00:00:00Z",
    "2026-04-31T00:00:00Z", "2026-13-01T00:00:00Z", "2026-01-00T00:00:00Z",
    "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z", "2026-01-01T00:00:60Z",
    "2026-01-01T00:00:00+24:00", "2026-01-01T00:00:00+01:60",
    "2026-01-01T00:00:00.1234567Z", "2026-01-01T00:00:00+0100", "2026-01-01T00:00:00z",
]) {
    test(`created_at does not normalize invalid date ${time}`, () => {
        const value = snapshot(); value.batches[0].created_at = time; assert.equal(read(value), null);
    });
}
for (const time of ["2000-02-29T23:59:59Z", "2026-10-08T00:00:00.1+05:30", "0001-01-01T00:00:00-23:59", "9999-12-31T23:59:59.999999Z"]) {
    test(`valid date remains exact ${time}`, () => {
        const value = snapshot(); value.batches[0].created_at = time; assert.deepEqual(read(value), value);
    });
}
test("microsecond ordering cannot use Date millisecond rounding", () => {
    const value = snapshot();
    value.batches = [
        { ...value.batches[0], batch_id: "1".repeat(32), created_at: "2026-10-08T00:00:00.000002Z" },
        { ...value.batches[0], batch_id: "2".repeat(32), created_at: "2026-10-08T00:00:00.000001Z" },
    ];
    assert.deepEqual(read(value), value); value.batches.reverse(); assert.equal(read(value), null);
});
test("equivalent timezone and fractional representations use stable ID ties", () => {
    const value = snapshot();
    value.batches = [
        { ...value.batches[0], batch_id: "2".repeat(32), created_at: "2026-10-08T01:00:00.1+01:00" },
        { ...value.batches[0], batch_id: "1".repeat(32), created_at: "2026-10-08T00:00:00.100000Z" },
    ];
    assert.deepEqual(read(value), value); value.batches.reverse(); assert.equal(read(value), null);
});
test("duplicate batch ID fails across different timestamps or spaces", () => {
    const value = snapshot();
    value.batches.push({ ...value.batches[0], created_at: "2000-01-01T00:00:00Z", space_id: "e".repeat(64) });
    assert.equal(read(value), null);
});
test("unknown coverage reason in twentieth item cannot be hidden by a valid prefix", () => {
    const value = snapshot("twenty"); value.batches[19].incomplete_reasons = ["PRIVATE"];
    assert.equal(read(value), null);
});
test("same space must retain a consistent dimension and model declaration", () => {
    const value = snapshot("twenty");
    value.batches[19].dimensions = 2;
    assert.equal(read(value), null);
});
for (const field of ["requested_model", "response_model"] as const) {
    test(`same space rejects conflicting ${field}`, () => {
        const value = snapshot("twenty");
        value.batches[19][field] = "other-model";
        assert.equal(read(value), null);
    });
}
test("different spaces may declare different dimensions and models", () => {
    const value = snapshot("twenty");
    Object.assign(value.batches[19], {
        space_id: "e".repeat(64), dimensions: 2,
        requested_model: "other-model", response_model: "other-version",
    });
    assert.deepEqual(read(value), value);
});
