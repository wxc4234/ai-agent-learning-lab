import assert from "node:assert/strict";
import { test } from "node:test";
import { resolve } from "node:path";

process.env.APP_MODE = "local";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
const { PUT } = await import("../../../src/app/api/workspaces/[workspaceId]/directory/route.ts");
const ID = "b".repeat(32);
const ROOT = resolve("project");
const DATA = { external_id: ID, name: "项目", root_path: ROOT };
const context = (id = ID) => ({ params: Promise.resolve({ workspaceId: id }) });
function request(body: unknown = { root_path: ROOT }, headers: Record<string, string | null> = {}, signal?: AbortSignal, host = "localhost") {
    const values = new Headers({ Origin: "http://localhost:3000", "Content-Type": "application/json; charset=utf-8", Cookie: "agent_session=forged", "X-Local-Runtime-Token": "forged", "X-User-ID": "99" });
    for (const [key, value] of Object.entries(headers)) {
        if (value === null) values.delete(key);
        else values.set(key, value);
    }
    return new Request(`http://${host}:3000/api/workspaces/${ID}/directory?user_id=99`, { method: "PUT", headers: values, body: JSON.stringify(body), signal });
}
async function error(response: Response, status: number, code: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(JSON.stringify(body).includes("PRIVATE"), false);
}

test("only forwards trusted headers and strips response extras", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(url, `http://127.0.0.1:8000/workspaces/${ID}/directory`);
        assert.equal(init.method, "PUT");
        assert.equal(init.cache, "no-store");
        assert.equal(init.redirect, "error");
        assert.ok(init.signal instanceof AbortSignal);
        assert.deepEqual(Object.fromEntries(new Headers(init.headers)), { "content-type": "application/json", origin: "http://localhost:3000", "x-local-runtime-token": "a".repeat(64) });
        assert.deepEqual(JSON.parse(String(init.body)), { root_path: " entry " });
        return Response.json({ ...DATA, user_id: 99, secret: "PRIVATE" }, { headers: { "Set-Cookie": "secret=PRIVATE", "X-Internal": "PRIVATE" } });
    });
    const response = await PUT(request({ root_path: " entry " }), context());
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), DATA);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("x-internal"), null);
    assert.deepEqual(response.headers.getSetCookie(), []);
    assert.equal(mock.mock.callCount(), 1);
});

for (const [key, value, code] of [
    ["APP_MODE", "account", "local_mode_required"], ["APP_MODE", "invalid", "local_access_rejected"],
    ["LOCAL_RUNTIME_TOKEN", "bad", "local_access_rejected"], ["API_BASE_URL", "https://evil.test", "local_access_rejected"],
] as const) {
    test(`rejects configuration ${key}:${value}`, async (t) => {
        const old = process.env[key];
        process.env[key] = value;
        t.after(() => { process.env[key] = old; });
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await PUT(request(), context()), 403, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [headers, status, code] of [
    [{ Origin: null }, 403, "workspace_origin_rejected"],
    [{ Origin: "https://evil.test" }, 403, "local_access_rejected"],
    [{ "Sec-Fetch-Site": "cross-site" }, 403, "local_access_rejected"],
    [{ "Content-Type": "text/plain" }, 415, "unsupported_workspace_content_type"],
] as [Record<string, string | null>, number, string][]) {
    test(`rejects headers ${JSON.stringify(headers)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await PUT(request(undefined, headers), context()), status, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("rejects remote frontend host", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
    await error(await PUT(request(undefined, {}, undefined, "evil.test"), context()), 403, "local_access_rejected");
    assert.equal(mock.mock.callCount(), 0);
});
for (const id of ["bad", "A".repeat(32), "a".repeat(31), "a".repeat(33), "../path"]) {
    test(`rejects identifier ${id}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await PUT(request(), context(id)), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const body of [null, [], {}, { root_path: "" }, { root_path: 1 }, { root_path: null }, { root_path: ROOT, user_id: 1 }]) {
    test(`rejects body ${JSON.stringify(body)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await PUT(request(body), context()), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("malformed JSON never fetches", async (t) => {
    const req = request();
    t.mock.method(req, "json", async () => { throw new SyntaxError("PRIVATE"); });
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
    await error(await PUT(req, context()), 400, "invalid_workspace_request");
    assert.equal(mock.mock.callCount(), 0);
});

const errors: [number, string][] = [
    [409, "proposal_application_busy"],
    [400, "invalid_workspace_request"], [403, "local_mode_required"], [403, "local_access_rejected"],
    [403, "workspace_origin_rejected"], [404, "workspace_not_accessible"], [409, "workspace_already_bound"],
    [415, "unsupported_workspace_content_type"], [422, "invalid_workspace_input"], [422, "invalid_directory_path"],
    [422, "directory_not_found"], [422, "directory_access_denied"], [422, "not_a_directory"],
    [422, "root_directory_not_allowed"], [500, "workspace_binding_failed"], [503, "directory_unavailable"],
];
for (const [status, code] of errors) {
    test(`safe error mapping ${status}:${code}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await error(await PUT(request(), context()), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, code] of [[409, "directory_not_found"], [422, "toString"], [422, "__proto__"], [418, "workspace_binding_failed"], [500, "unknown"]] as const) {
    test(`rejects unknown or mismatched error ${status}:${code}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await error(await PUT(request(), context()), 502, "invalid_backend_response");
    });
}
for (const payload of [null, [], { ...DATA, external_id: "c".repeat(32) }, { ...DATA, name: "" }, { ...DATA, name: "😀".repeat(101) }, { ...DATA, root_path: "relative" }, { ...DATA, root_path: null }, { ...DATA, root_path: `${ROOT}\0` }]) {
    test(`rejects invalid upstream payload ${JSON.stringify(payload)}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(payload));
        await error(await PUT(request(), context()), 502, "invalid_backend_response");
    });
}
test("accepts 100 Unicode code points", async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ ...DATA, name: "😀".repeat(100) }));
    assert.equal((await PUT(request(), context())).status, 200);
});
for (const kind of ["network", "json"]) {
    test(`upstream ${kind} failure is uncertain and not retried`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (kind === "network") throw new TypeError("PRIVATE");
            return new Response("PRIVATE invalid JSON");
        });
        const response = await PUT(request(), context());
        const clone = response.clone();
        await error(response, 502, kind === "network" ? "workspace_binding_unavailable" : "invalid_backend_response");
        assert.match((await clone.json()).message, /尚未确认/);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const phase of ["before", "request-json", "fetch", "response-json"]) {
    for (const kind of phase === "before" || phase === "request-json" ? ["client"] : ["client", "timeout"]) {
        test(`${kind} abort during ${phase}`, async (t) => {
            const client = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, "timeout", () => timeout.signal);
            const abort = () => (kind === "client" ? client : timeout).abort();
            const req = request(undefined, {}, client.signal);
            if (phase === "before") abort();
            if (phase === "request-json") t.mock.method(req, "json", async () => { abort(); throw new Error("aborted"); });
            const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                const signal = init.signal as AbortSignal;
                if (phase === "fetch") { abort(); signal.throwIfAborted(); }
                const response = Response.json(DATA);
                if (phase === "response-json") t.mock.method(response, "json", async () => { abort(); signal.throwIfAborted(); return DATA; });
                return response;
            });
            await error(await PUT(req, context()), kind === "client" ? 499 : 504, kind === "client" ? "workspace_binding_cancelled" : "workspace_binding_timeout");
            assert.equal(mock.mock.callCount(), phase === "before" || phase === "request-json" ? 0 : 1);
        });
    }
}
