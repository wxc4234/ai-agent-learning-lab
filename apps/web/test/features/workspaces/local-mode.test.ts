import assert from "node:assert/strict";
import { test } from "node:test";

process.env.APP_MODE = "local";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
const { POST: create } = await import("../../../src/app/api/workspaces/route.ts");
const { POST: chat } = await import("../../../src/app/api/chat/stream/route.ts");
const { POST: cancel } = await import("../../../src/app/api/runs/[runId]/cancel/route.ts");
const { GET: me } = await import("../../../src/app/api/auth/me/route.ts");
const { POST: register } = await import("../../../src/app/api/auth/register/route.ts");
const { localHeaders } = await import("../../../src/app/api/_shared/runtime.ts");
const RESULT = { external_id: "a".repeat(32), name: "项目", created_at: "2026-09-15T08:00:00Z" };
function request(path = "/api/workspaces", headers: Record<string, string> = {}): Request {
    return new Request(`http://localhost:3000${path}`, { method: "POST", headers: {
        Origin: "http://localhost:3000", "Content-Type": "application/json",
        Cookie: "agent_session=invalid", "X-Local-Runtime-Token": "forged", ...headers,
    }, body: JSON.stringify({ name: "项目", reason: "user_cancelled" }) });
}

test("local creation uses only server token and no browser identity", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
        const headers = new Headers(init.headers);
        assert.equal(headers.get("x-local-runtime-token"), "a".repeat(64));
        assert.equal(headers.get("cookie"), null);
        return Response.json(RESULT, { status: 201 });
    });
    const req = request();
    t.mock.method(req, "json", async () => ({ name: "项目" }));
    const response = await create(req);
    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), RESULT);
    assert.equal(response.headers.get("x-local-runtime-token"), null);
    assert.equal(mock.mock.callCount(), 1);
});

test("local stream remains streamed and cancellation needs no cookie", async (t) => {
    t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(new Headers(init.headers).get("cookie"), null);
        assert.equal(new Headers(init.headers).get("x-local-runtime-token"), "a".repeat(64));
        assert.ok(init.signal);
        assert.equal(init.redirect, "error");
        if (url.endsWith("/cancel")) return new Response(null, { status: 204 });
        return new Response('{"type":"RUN_FINISHED"}\n', { headers: { "Content-Type": "application/x-ndjson", "X-Run-ID": "1" } });
    });
    const stream = await chat(request("/api/chat/stream"));
    assert.equal(stream.status, 200);
    assert.equal(await stream.text(), '{"type":"RUN_FINISHED"}\n');
    assert.equal((await cancel(request("/api/runs/1/cancel"), { params: Promise.resolve({ runId: "1" }) })).status, 204);
});

test("local identity probe needs no cookie and registration is disabled", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ external_id: "local-owner-v1", username: "本机用户" }));
    assert.equal((await me(new Request("http://localhost:3000/api/auth/me"))).status, 200);
    assert.equal((await register(request())).status, 403);
    assert.equal(mock.mock.callCount(), 1);
});

for (const [label, url, headers] of [
    ["foreign host", "http://evil.test/api/auth/me", {}],
    ["foreign origin", "http://localhost:3000/api/auth/me", { Origin: "https://evil.test" }],
    ["cross site", "http://localhost:3000/api/auth/me", { "Sec-Fetch-Site": "cross-site" }],
] as const) {
    test(`blocks ${label} before fetch`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({}));
        assert.equal((await me(new Request(url, { headers }))).status, 502);
        assert.equal(mock.mock.callCount(), 0);
    });
}

test("missing token and remote backend fail closed", () => {
    const token = process.env.LOCAL_RUNTIME_TOKEN;
    const backend = process.env.API_BASE_URL;
    try {
        process.env.LOCAL_RUNTIME_TOKEN = "";
        assert.throws(() => localHeaders(request()));
        process.env.LOCAL_RUNTIME_TOKEN = token;
        process.env.API_BASE_URL = "https://remote.test";
        assert.throws(() => localHeaders(request()));
    } finally {
        process.env.LOCAL_RUNTIME_TOKEN = token;
        process.env.API_BASE_URL = backend;
    }
});
