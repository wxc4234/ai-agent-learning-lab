import assert from "node:assert/strict";
import { test } from "node:test";

process.env.APP_MODE = "account";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
const { GET } = await import("../../../src/app/api/workspaces/route.ts");
const COOKIE = `agent_session=${"x".repeat(43)}`;
const SECRET = "INTERNAL_PRIVATE";
const DATA = { items: [{ external_id: "a".repeat(32), name: "项目", created_at: "2026-09-15T08:00:00Z" }], has_more: false };
function request(query = "", headers: Record<string, string | null> = {}, signal?: AbortSignal): Request {
    const values = new Headers({ Cookie: COOKIE, "X-User-ID": "forged", "X-Local-Runtime-Token": "forged" });
    for (const [name, value] of Object.entries(headers)) {
        if (value === null) values.delete(name);
        else values.set(name, value);
    }
    return new Request(`http://localhost:3000/api/workspaces${query}`, { headers: values, signal });
}
async function error(response: Response, status: number, code: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(JSON.stringify(body).includes(SECRET), false);
}

for (const mode of ["account", "local"]) {
    for (const [query, limit] of [["", 20], ["?limit=1&user_id=99", 1], ["?limit=100", 100]] as const) {
        test(`${mode}: forwards only trusted identity and limit ${limit}`, async (t) => {
            process.env.APP_MODE = mode;
            t.after(() => { process.env.APP_MODE = "account"; });
            const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
                assert.equal(url, `http://127.0.0.1:8000/workspaces?limit=${limit}`);
                assert.equal(init.method, "GET");
                assert.equal(init.body, undefined);
                assert.equal(init.cache, "no-store");
                assert.equal(init.redirect, "error");
                assert.ok(init.signal instanceof AbortSignal);
                assert.deepEqual(Object.fromEntries(new Headers(init.headers)), mode === "local"
                    ? { "x-local-runtime-token": "a".repeat(64) } : { cookie: COOKIE });
                return Response.json({ ...DATA, items: [{ ...DATA.items[0], user_id: 1 }], secret: SECRET }, {
                    headers: { "Set-Cookie": "private=value", "X-Internal": SECRET },
                });
            });
            const response = await GET(request(query, { Cookie: mode === "local" ? null : `theme=dark; ${COOKIE}` }));
            assert.equal(response.status, 200);
            assert.deepEqual(await response.json(), DATA);
            assert.equal(response.headers.get("cache-control"), "no-store");
            assert.deepEqual(response.headers.getSetCookie(), []);
            assert.equal(response.headers.get("x-internal"), null);
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}

for (const query of ["?limit=0", "?limit=-1", "?limit=101", "?limit=1.5", "?limit=", "?limit=01", "?limit=1&limit=2", "?limit=abc"]) {
    test(`rejects ${query} without fetch`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request(query)), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const cookie of [null, "agent_session=bad", `${COOKIE}; ${COOKIE}`]) {
    test(`account rejects cookie ${cookie}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request("", { Cookie: cookie })), 401, "invalid_login_session");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const headers of [{ Origin: "https://evil.test" }, { "Sec-Fetch-Site": "cross-site" }] as Record<string, string>[]) {
    test(`rejects foreign request ${JSON.stringify(headers)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request("", headers)), 403, "workspace_origin_rejected");
        assert.equal(mock.mock.callCount(), 0);
    });
}

test("accepts and forwards a present allowed Origin", async (t) => {
    t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
        assert.equal(new Headers(init.headers).get("origin"), "http://localhost:3000");
        return Response.json(DATA);
    });
    assert.equal((await GET(request("", { Origin: "http://localhost:3000" }))).status, 200);
});

for (const [status, code] of [[400, "invalid_workspace_request"], [401, "invalid_login_session"], [403, "local_access_rejected"], [422, "invalid_workspace_input"], [500, "workspace_list_failed"]] as const) {
    test(`maps ${status} safely`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: SECRET, details: SECRET }, { status }));
        await error(await GET(request()), status, code);
    });
}
for (const [label, body, status] of [["invalid list", {}, 200], ["creation error", { code: "workspace_creation_failed" }, 500], ["unknown status", { code: "workspace_list_failed" }, 503]] as const) {
    test(`rejects ${label}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(body, { status }));
        await error(await GET(request()), 502, "invalid_backend_response");
    });
}
for (const stage of ["json", "connection"]) {
    test(`sanitizes ${stage}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => {
            if (stage === "connection") throw new Error(SECRET);
            return new Response(SECRET);
        });
        await error(await GET(request()), 502, stage === "json" ? "invalid_backend_response" : "workspace_list_unavailable");
    });
}
for (const cause of ["client", "timeout"] as const) {
    for (const stage of ["fetch", "body"]) {
        test(`${cause} during ${stage}`, async (t) => {
            const client = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, "timeout", (ms: number) => { assert.equal(ms, 10000); return timeout.signal; });
            t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                const signal = init.signal;
                assert.ok(signal);
                const stalled = () => new Promise<never>((_resolve, reject) => {
                    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                    queueMicrotask(() => (cause === "client" ? client : timeout).abort());
                });
                if (stage === "fetch") return stalled();
                const response = Response.json(DATA);
                t.mock.method(response, "json", stalled);
                return response;
            });
            await error(await GET(request("", {}, client.signal)), cause === "client" ? 499 : 504,
                cause === "client" ? "workspace_list_cancelled" : "workspace_list_timeout");
        });
    }
}
