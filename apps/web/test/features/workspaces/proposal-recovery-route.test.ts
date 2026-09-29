import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { proposalRecoveryProxy } from "../../../src/app/api/_shared/proposal-recovery-proxy.ts";
const original = { ...process.env };
afterEach(() => { process.env = { ...original }; });
const ids = ["a".repeat(32), "b".repeat(32), "c".repeat(32)] as const;
const base = { workspace_id: ids[0], task_id: ids[1], proposal_id: ids[2] };
const origin = "http://localhost:3000";
for (const restore of [true, false]) {
    for (const kind of ["valid", "foreign", "malformed", "transport", "injection", "account", "aborted"]) {
        test(`${restore ? 'restore' : 'audit'} ${kind}`, async t => {
            process.env.APP_MODE = kind === "account" ? "account" : "local";
            process.env.LOCAL_RUNTIME_TOKEN = "d".repeat(64); process.env.AUTH_ALLOWED_ORIGINS = origin;
            const controller = new AbortController(); if (kind === "aborted") controller.abort();
            const responseBody = { ...base, ...(restore ? { restore_proposal_id: "e".repeat(32) } : {
                events: [{ event: "applied", created_at: "2026-09-29T00:00:00Z", restore_proposal_id: null, private_path: "/secret" }],
            }), private_path: "/secret" };
            if (kind === "foreign") responseBody.task_id = "f".repeat(32);
            if (kind === "malformed") Object.assign(responseBody, restore ? { restore_proposal_id: "bad" } : { events: [{ event: "unknown" }] });
            const mocked = t.mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
                if (kind === "transport") throw new Error("private exception");
                assert.equal(init.redirect, "error");
                if (restore) assert.deepEqual(JSON.parse(String(init.body)), { action: "restore" });
                return Response.json(responseBody);
            });
            const request = new Request(origin + "/api/test" + (!restore && kind === "injection" ? "?path=/secret" : ""), {
                method: restore ? "POST" : "GET", signal: controller.signal,
                ...(restore ? { headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify({ action: "restore", ...(kind === "injection" ? { path: "/secret" } : {}) }) } : {}),
            });
            const response = await proposalRecoveryProxy(request, ...ids, restore);
            assert.equal(response.status, kind === "valid" ? 200 : kind === "account" ? 403 : kind === "injection" ? 422 : kind === "aborted" ? 499 : 502);
            assert.equal(mocked.mock.callCount(), ["account", "injection", "aborted"].includes(kind) ? 0 : 1);
            assert.ok(!(await response.text()).includes("private"));
        });
    }
}
