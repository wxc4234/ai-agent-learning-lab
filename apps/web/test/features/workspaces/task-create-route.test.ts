import assert from "node:assert/strict";
import { test } from "node:test";

process.env.APP_MODE = "local";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
const { POST } = await import("../../../src/app/api/workspaces/[workspaceId]/tasks/route.ts");
const ID = "b".repeat(32);
const DATA = { external_id: "c".repeat(32), workspace_id: ID, conversation_id: "d".repeat(32), title: "任务", created_at: "2026-09-15T08:00:00Z" };
const context = (id = ID) => ({ params: Promise.resolve({ workspaceId: id }) });
function request(body: unknown = { title: " 任务 " }, overrides: Record<string, string | null> = {}, signal?: AbortSignal, host = "localhost") {
    const headers = new Headers({ Origin: "http://localhost:3000", "Content-Type": "application/json; charset=utf-8", Cookie: "PRIVATE", "X-Local-Runtime-Token": "forged", "X-User-ID": "99" });
    for (const [key, value] of Object.entries(overrides)) {
        if (value === null) headers.delete(key);
        else headers.set(key, value);
    }
    return new Request(`http://${host}:3000/api/workspaces/${ID}/tasks?user_id=99`, { method: "POST", body: JSON.stringify(body), headers, signal });
}
async function error(response: Response, status: number, code: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.ok(!JSON.stringify(body).includes("PRIVATE"));
    if (code === "task_creation_uncertain") assert.match(body.message, /未确认/);
}

// 验证真实 Request/Response 边界，只有上游 fetch 使用替身。
test("forwards only trusted fields and headers and preserves 201", async t => {
    const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(url, `http://127.0.0.1:8000/workspaces/${ID}/tasks`);
        assert.equal(init.method, "POST");
        assert.equal(init.cache, "no-store");
        assert.equal(init.redirect, "error");
        assert.ok(init.signal instanceof AbortSignal);
        assert.deepEqual(JSON.parse(String(init.body)), { title: " 任务 " });
        assert.deepEqual(Object.fromEntries(new Headers(init.headers)), { origin: "http://localhost:3000", "content-type": "application/json", "x-local-runtime-token": "a".repeat(64) });
        return Response.json({ ...DATA, user_id: 99 }, { status: 201, headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" } });
    });
    const response = await POST(request(), context());
    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), DATA);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("x-internal"), null);
    assert.deepEqual(response.headers.getSetCookie(), []);
    assert.equal(mock.mock.callCount(), 1);
});
for (const [key, value, code] of [
    ["APP_MODE", "account", "local_mode_required"], ["APP_MODE", "bad", "local_access_rejected"],
    ["API_BASE_URL", "https://evil.test", "local_access_rejected"], ["LOCAL_RUNTIME_TOKEN", "bad", "local_access_rejected"],
] as const) {
    test(`rejects configuration ${key}`, async t => {
        const old = process.env[key];
        process.env[key] = value;
        t.after(() => { if (old === undefined) delete process.env[key]; else process.env[key] = old; });
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await POST(request(), context()), 403, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [headers, status, code] of [
    [{ Origin: null }, 403, "workspace_origin_rejected"],
    [{ Origin: "https://evil.test" }, 403, "local_access_rejected"],
    [{ "Sec-Fetch-Site": "cross-site" }, 403, "local_access_rejected"],
    [{ "Content-Type": "text/plain" }, 415, "unsupported_workspace_content_type"],
] as [Record<string, string | null>, number, string][]) {
    test(`rejects headers ${JSON.stringify(headers)}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await POST(request(undefined, headers), context()), status, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("rejects remote frontend", async t => {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
    await error(await POST(request(undefined, {}, undefined, "evil.test"), context()), 403, "local_access_rejected");
    assert.equal(mock.mock.callCount(), 0);
});
for (const id of ["bad", "A".repeat(32), "a".repeat(31), "a".repeat(33), "../tasks"]) {
    test(`rejects identifier ${id}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await POST(request(), context(id)), 422, "invalid_task_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const body of [null, [], {}, { title: 1 }, { title: null }, { title: true }, { title: "任务", user_id: 1 }, { title: "任务", conversation_id: ID }]) {
    test(`rejects body ${JSON.stringify(body)}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await POST(request(body), context()), 422, "invalid_task_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("malformed JSON never reaches upstream", async t => {
    const req = request();
    t.mock.method(req, "json", async () => { throw new SyntaxError("PRIVATE"); });
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
    await error(await POST(req, context()), 400, "invalid_task_request");
    assert.equal(mock.mock.callCount(), 0);
});
for (const [status, code] of [
    [400, "invalid_workspace_request"], [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"],
    [404, "workspace_not_accessible"], [415, "unsupported_workspace_content_type"], [422, "invalid_task_input"], [422, "invalid_task_title"], [500, "task_creation_uncertain"],
] as const) {
    test(`maps ${status}:${code}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await error(await POST(request(), context()), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, code] of [[422, "toString"], [422, "__proto__"], [404, "invalid_task_input"], [503, "task_creation_uncertain"], [200, "invalid_task_title"]] as const) {
    test(`mismatched error ${status}:${code} is uncertain`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code }, { status }));
        await error(await POST(request(), context()), 502, "task_creation_uncertain");
    });
}
for (const payload of [null, [], { ...DATA, workspace_id: "f".repeat(32) }, { ...DATA, external_id: "bad" }, { ...DATA, conversation_id: null }, { ...DATA, title: "" }, { ...DATA, title: "😀".repeat(201) }, { ...DATA, created_at: "invalid" }, { ...DATA, created_at: 123 }]) {
    test(`invalid success ${JSON.stringify(payload)}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json(payload, { status: 201 }));
        await error(await POST(request(), context()), 502, "task_creation_uncertain");
    });
}
test("accepts Unicode title boundary", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ ...DATA, title: "😀".repeat(200) }, { status: 201 }));
    assert.equal((await POST(request(), context())).status, 201);
});
for (const kind of ["network", "json"]) {
    test(`${kind} failure does not retry`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (kind === "network") throw new Error("PRIVATE");
            return new Response("PRIVATE", { status: 201 });
        });
        await error(await POST(request(), context()), 502, "task_creation_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
// 前置取消明确未转发；后置取消只报告结果未知，包括正文已返回时的竞态。
for (const phase of ["before", "request-json", "after-request-json", "fetch", "response-json", "after-response-json"]) {
    for (const kind of phase.includes("request-json") || phase === "before" ? ["client"] : ["client", "timeout"]) {
        test(`${kind} abort ${phase}`, async t => {
            const client = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, "timeout", (ms: number) => { assert.equal(ms, 10000); return timeout.signal; });
            const abort = () => (kind === "client" ? client : timeout).abort();
            const req = request(undefined, {}, client.signal);
            const beforeFetch = phase === "before" || phase.includes("request-json");
            if (phase === "before") abort();
            if (phase.includes("request-json")) t.mock.method(req, "json", async () => {
                abort();
                if (phase === "request-json") throw new Error("aborted");
                return { title: "任务" };
            });
            const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                if (phase === "fetch") { abort(); init.signal?.throwIfAborted(); }
                const response = Response.json(DATA, { status: 201 });
                if (phase.includes("response-json")) t.mock.method(response, "json", async () => {
                    abort();
                    if (phase === "response-json") init.signal?.throwIfAborted();
                    return DATA;
                });
                return response;
            });
            await error(await POST(req, context()), kind === "client" ? 499 : 504, beforeFetch ? "task_request_cancelled" : "task_creation_uncertain");
            assert.equal(mock.mock.callCount(), beforeFetch ? 0 : 1);
        });
    }
}
