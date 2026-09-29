import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { GET } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/git/staged/route.ts";
import { stagedBindingProxy } from "../../../src/app/api/_shared/staged-proxy.ts";
import { readStagedData, MAX_STAGED_BYTES } from "../../../src/features/workbench/staged-data.ts";
const saved = { ...process.env };
const workspaceId = "a".repeat(32), taskId = "b".repeat(32), token = "e".repeat(64);
const context = { params: Promise.resolve({ workspaceId, taskId }) };
const path = `/workspaces/${workspaceId}/tasks/${taskId}/git/staged`;
const payload = () => ({ workspace_id: workspaceId, task_id: taskId, binding_revision: 1, scope: "staged_only", status: "staged_compared", unavailable_reasons: [], changes: [] });
const req = (query = "binding_revision=1", init: RequestInit = {}) => new Request(`http://localhost:3000/api${path}?${query}`, init);
beforeEach(() => Object.assign(process.env, { APP_MODE: "local", API_BASE_URL: "http://127.0.0.1:8000", LOCAL_RUNTIME_TOKEN: token }));
afterEach(() => { process.env = { ...saved }; });
test("route uses server credentials and fixed address", async t => {
    t.mock.method(globalThis, "fetch", async (url: unknown, init?: RequestInit) => {
        assert.equal(url, `http://127.0.0.1:8000${path}?binding_revision=1`);
        assert.deepEqual([...new Headers(init?.headers)], [["x-local-runtime-token", token]]);
        assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error");
        return Response.json(payload(), { headers: { "Set-Cookie": "private" } });
    });
    const r = await GET(req(undefined, { headers: { "X-Local-Runtime-Token": "forged" } }), context);
    assert.equal(r.status, 200); assert.deepEqual(await r.json(), payload());
    assert.equal(r.headers.get("cache-control"), "no-store"); assert.equal(r.headers.get("set-cookie"), null);
});
for (const query of ["", "binding_revision=0", "binding_revision=01", "binding_revision=1&binding_revision=1", "binding_revision=1&path=/private", "binding_revision=9007199254740992"]) {
    test(`invalid query ${query}`, async t => { t.mock.method(globalThis, "fetch", () => assert.fail("no fetch")); assert.equal((await GET(req(query), context)).status, 422); });
}
for (const changed of [{ task_id: "f".repeat(32) }, { binding_revision: 2 }, { changes: null }, { root: "/private" }, { status: "comparison_unavailable" }, { scope: "worktree" }]) {
    test(`invalid receipt ${JSON.stringify(changed)}`, async t => { t.mock.method(globalThis, "fetch", async () => Response.json({ ...payload(), ...changed })); assert.equal((await GET(req(), context)).status, 502); });
}
test("missing stays unknown", async t => {
    const data = { ...payload(), status: "comparison_unavailable", changes: null, unavailable_reasons: ["index_missing"] };
    t.mock.method(globalThis, "fetch", async () => Response.json(data)); assert.deepEqual(await (await GET(req(), context)).json(), data);
});
for (const [status, code] of [[409, "staged_observation_changed"], [422, "staged_observation_unsupported"], [500, "staged_read_failed"]] as const) {
    test(`error ${status}`, async t => { t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "/private" }, { status })); const r = await GET(req(), context); assert.equal(r.status, status); assert.equal((await r.json()).code, code); });
}
test("stream byte budget", async t => {
    let canceled = false;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_STAGED_BYTES + 1)); }, cancel() { canceled = true; } }), { headers: { "Content-Type": "application/json" } }));
    assert.equal((await GET(req(), context)).status, 502); assert.equal(canceled, true);
});
test("abort during body consumption", async t => {
    const c = new AbortController(); let canceled = false;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ pull() { queueMicrotask(() => c.abort()); }, cancel() { canceled = true; } }), { headers: { "Content-Type": "application/json" } }));
    assert.equal((await GET(req(undefined, { signal: c.signal }), context)).status, 499); assert.equal(canceled, true);
});
test("abort before fetch", async t => { t.mock.method(globalThis, "fetch", () => assert.fail("no fetch")); assert.equal((await GET(req(undefined, { signal: AbortSignal.abort() }), context)).status, 499); });
const version = { mode: 0o100644, object_id: "1".repeat(40) };
for (const item of [{ path: "../outside", status: "added", head: null, index: [{ stage: 0, version }] }, { path: "file", status: "modified", head: version, index: [{ stage: 0, version }] }, { path: "file", status: "unmerged", head: null, index: [{ stage: 0, version }] }, { path: "file", status: "deleted", head: null, index: [] }]) {
    test(`invalid item ${item.status}`, () => assert.equal(readStagedData({ ...payload(), changes: [item] }, workspaceId, taskId, 1), null));
}
test("conflict projection", () => { const data = { ...payload(), changes: [{ path: "中文/file", status: "unmerged", head: version, index: [{ stage: 2, version }] }] }; assert.deepEqual(readStagedData(data, workspaceId, taskId, 1), data); });

for (const changed of [{}, { task_id: "wrong" }, { binding_revision: 0 }, { bound: "yes" }]) {
    test(`binding metadata ${JSON.stringify(changed)}`, async t => {
        const value = { workspace_id: workspaceId, task_id: taskId, binding_revision: 1, bound: true, ...changed };
        t.mock.method(globalThis, "fetch", async () => Response.json(value));
        const response = await stagedBindingProxy(new Request("http://localhost:3000/api/binding"), workspaceId, taskId);
        assert.equal(response.status, Object.keys(changed).length ? 502 : 200);
    });
}
test("rejects malformed unicode path", () => {
    assert.equal(readStagedData({ ...payload(), changes: [{ path: String.fromCharCode(0xd800), status: "added", head: null, index: [{ stage: 0, version }] }] }, workspaceId, taskId, 1), null);
});
