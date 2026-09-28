import assert from "node:assert/strict";
import { test } from "node:test";
import { grantChangeObserved, readGrantReceipt } from "../../../src/features/workbench/project-write-grant-data.ts";
const ids = ["a".repeat(32), "b".repeat(32), "c".repeat(32)] as const;
const enabled = { grant_id: "d".repeat(32), revision: 1, status: "enabled" } as const;
const revoked = { ...enabled, revision: 2, status: "revoked" } as const;
const raw = { workspace_id: ids[0], task_id: ids[1], proposal_id: ids[2], grant: enabled };
test("public receipt strips internal fields and distinguishes null", () => {
    assert.deepEqual(readGrantReceipt({ ...raw, target: "PRIVATE", grant: { ...enabled, target: "PRIVATE" } }, ...ids), { grant: enabled });
    assert.deepEqual(readGrantReceipt({ ...raw, grant: null }, ...ids), { grant: null });
    assert.deepEqual(readGrantReceipt({ ...raw, grant: revoked }, ...ids), { grant: revoked });
});
for (const [i, bad] of [null, [], {}, { ...raw, task_id: "other" }, { ...raw, grant: undefined }, { ...raw, grant: {} }, { ...raw, grant: { ...enabled, revision: true } }, { ...raw, grant: { ...enabled, status: "revoked" } }].entries()) {
    test(`malformed receipt ${i} stays unknown`, () => assert.equal(readGrantReceipt(bad, ...ids), null));
}
test("negative query cannot prove an unknown write has stopped", () => {
    assert.equal(grantChangeObserved("issue", null), false);
    assert.equal(grantChangeObserved(`revoke:${enabled.grant_id}`, enabled), false);
    assert.equal(grantChangeObserved("corrupt", null), false);
    assert.equal(grantChangeObserved("corrupt", revoked), false);
    assert.equal(grantChangeObserved("issue", enabled), true);
    assert.equal(grantChangeObserved("issue", revoked), true);
    assert.equal(grantChangeObserved(`revoke:${enabled.grant_id}`, revoked), true);
    assert.equal(grantChangeObserved("revoke:other", revoked), false);
    assert.equal(grantChangeObserved(null, null), true);
});
