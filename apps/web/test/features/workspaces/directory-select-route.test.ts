import assert from "node:assert/strict";
import { test } from "node:test";

process.env.APP_MODE = "local";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
const { POST } = await import("../../../src/app/api/workspaces/[workspaceId]/directory/select/route.ts");
const ID = "b".repeat(32);
const DATA = { external_id: ID, name: "项目", root_path: "/tmp/project" };
const context = (id = ID) => ({ params: Promise.resolve({ workspaceId: id }) });
function request(body: unknown = {}, headers: Record<string, string> = {}, signal?: AbortSignal) {
    return new Request(`http://localhost:3000/api/workspaces/${ID}/directory/select?user_id=99`, {
        method: "POST", body: JSON.stringify(body), signal,
        headers: { Origin: "http://localhost:3000", "Content-Type": "application/json", Cookie: "PRIVATE", "X-Local-Runtime-Token": "forged", ...headers },
    });
}
async function check(response: Response, status: number, code?: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    if (code) {
        const payload = await response.json();
        assert.equal(payload.code, code);
        assert.equal(JSON.stringify(payload).includes("PRIVATE"), false);
    }
}
for (const status of [200, 204]) {
    test(`selected or cancelled ${status}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
            assert.equal(url, `http://127.0.0.1:8000/workspaces/${ID}/directory/select`);
            assert.equal(init.body, "{}");
            assert.equal(init.method, "POST");
            assert.equal(init.redirect, "error");
            assert.equal(init.cache, "no-store");
            assert.deepEqual(Object.fromEntries(new Headers(init.headers)), { origin: "http://localhost:3000", "content-type": "application/json", "x-local-runtime-token": "a".repeat(64) });
            return status === 204 ? new Response(null, { status }) : Response.json({ ...DATA, extra: "PRIVATE" });
        });
        const response = await POST(request(), context());
        await check(response, status);
        if (status === 200) assert.deepEqual(await response.json(), DATA);
        else assert.equal(await response.text(), "");
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const body of [null, [], { path: "/PRIVATE" }, { command: "PRIVATE" }]) {
    test(`rejects supplied arguments ${JSON.stringify(body)}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await check(await POST(request(body), context()), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const headers of [{ Origin: "" }, { Origin: "https://evil.test" }, { "Sec-Fetch-Site": "cross-site" }] as Record<string, string>[]) {
    test(`rejects origin ${JSON.stringify(headers)}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await check(await POST(request({}, headers), context()), 403);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [status, code] of [ [409, "proposal_application_busy"],[408, "directory_picker_timeout"], [409, "directory_picker_busy"], [501, "directory_picker_unsupported"], [503, "directory_picker_unavailable"], [409, "workspace_already_bound"], [500, "workspace_binding_failed"]] as const) {
    test(`maps ${code}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await check(await POST(request(), context()), status, code);
    });
}
for (const payload of [null, { ...DATA, root_path: null }, { ...DATA, external_id: "c".repeat(32) }, { ...DATA, root_path: "relative" }, { ...DATA, name: "" }]) {
    test(`invalid success remains uncertain ${JSON.stringify(payload)}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json(payload));
        await check(await POST(request(), context()), 502, "directory_selection_uncertain");
    });
}
for (const kind of ["client", "timeout", "network", "json", "mismatched-code"]) {
    test(`failure ${kind} never retries`, async t => {
        const client = new AbortController();
        const timeout = new AbortController();
        t.mock.method(AbortSignal, "timeout", (ms: number) => { assert.equal(ms, 130_000); return timeout.signal; });
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (kind === "network") throw new Error("PRIVATE");
            if (kind === "client") client.abort();
            if (kind === "timeout") timeout.abort();
            if (kind === "json") return new Response("PRIVATE");
            if (kind === "mismatched-code") return Response.json({ code: "directory_picker_busy" }, { status: 500 });
            return Response.json(DATA);
        });
        await check(await POST(request({}, {}, client.signal), context()), kind === "client" ? 499 : 502, "directory_selection_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
