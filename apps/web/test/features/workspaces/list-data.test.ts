import assert from "node:assert/strict";
import { test } from "node:test";
import { readWorkspaceList } from "../../../src/features/workspaces/workspace-list.ts";

const ITEM = { external_id: "a".repeat(32), name: "项目", created_at: "2026-09-15T08:00:00.123456+00:00" };
const data = (items: unknown[], more = false) => ({ items, has_more: more });

test("accepts empty and truncated lists with public fields only", () => {
    assert.deepEqual(readWorkspaceList(data([]), 20), data([]));
    assert.deepEqual(readWorkspaceList({ ...data([{ ...ITEM, user_id: 1, secret: "hidden" }], true), total: 99 }, 1), data([ITEM], true));
});

test("accepts Unicode code points rather than UTF-16 units", () => {
    const item = { ...ITEM, name: "𠀀".repeat(100) };
    assert.deepEqual(readWorkspaceList(data([item]), 20), data([item]));
    assert.equal(readWorkspaceList(data([{ ...item, name: item.name + "𠀀" }]), 20), null);
});

for (const [label, payload, limit] of [
    ["null", null, 20], ["array", [], 20], ["missing items", {}, 20],
    ["wrong items", { items: {}, has_more: false }, 20],
    ["wrong flag", { items: [], has_more: "false" }, 20],
    ["over limit", data([ITEM, { ...ITEM, external_id: "b".repeat(32) }]), 1],
    ["inconsistent flag", data([], true), 20],
    ["duplicate ids", data([ITEM, ITEM]), 20],
    ["non object item", data([null]), 20],
    ["invalid id", data([{ ...ITEM, external_id: "bad" }]), 20],
    ["numeric id", data([{ ...ITEM, external_id: 1 }]), 20],
    ["empty name", data([{ ...ITEM, name: "" }]), 20],
    ["numeric name", data([{ ...ITEM, name: 1 }]), 20],
    ["invalid date", data([{ ...ITEM, created_at: "bad" }]), 20],
    ["numeric date", data([{ ...ITEM, created_at: 1 }]), 20],
] as const) {
    test(`rejects ${label}`, () => assert.equal(readWorkspaceList(payload, limit), null));
}
