import assert from "node:assert/strict";
import { test } from "node:test";
import { resolve } from "node:path";

process.env.APP_MODE = "local";
process.env.API_BASE_URL = "http://127.0.0.1:8000";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000";
process.env.LOCAL_RUNTIME_TOKEN = "a".repeat(64);
const { GET } = await import("../../../src/app/api/workspaces/[workspaceId]/directory/route.ts");
const ID = "b".repeat(32);
const DATA = { external_id: ID, name: "项目", root_path: resolve("project") };
const context = (id = ID) => ({ params: Promise.resolve({ workspaceId: id }) });

function request(headers: Record<string, string> = {}, signal?: AbortSignal, host = "localhost") {
    return new Request(`http://${host}:3000/api/workspaces/${ID}/directory?user_id=99`, {
        headers: { Cookie: "agent_session=forged", "X-Local-Runtime-Token": "forged", "X-User-ID": "99", ...headers },
        signal,
    });
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

// 未绑定和已绑定均为成功；无 Origin 的同源读取也应保留严格的凭证边界。
for (const root_path of [null, DATA.root_path]) {
    for (const origin of [undefined, "http://localhost:3000"]) {
        test(`reads ${root_path} with origin ${origin}`, async (t) => {
            const expected = { ...DATA, root_path };
            const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
                assert.equal(url, `http://127.0.0.1:8000/workspaces/${ID}/directory`);
                assert.equal(init.method, "GET");
                assert.equal(init.body, undefined);
                assert.equal(init.cache, "no-store");
                assert.equal(init.redirect, "error");
                assert.ok(init.signal instanceof AbortSignal);
                assert.deepEqual(Object.fromEntries(new Headers(init.headers)), {
                    ...(origin ? { origin } : {}), "x-local-runtime-token": "a".repeat(64),
                });
                return Response.json({ ...expected, secret: "PRIVATE" }, { headers: { "Set-Cookie": "secret=PRIVATE", "X-Internal": "PRIVATE" } });
            });
            const response = await GET(request(origin ? { Origin: origin } : {}), context());
            assert.equal(response.status, 200);
            assert.deepEqual(await response.json(), expected);
            assert.equal(response.headers.get("cache-control"), "no-store");
            assert.deepEqual(response.headers.getSetCookie(), []);
            assert.equal(response.headers.get("x-internal"), null);
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}

for (const [key, value, code] of [
    ["APP_MODE", "account", "local_mode_required"], ["APP_MODE", "invalid", "local_access_rejected"],
    ["LOCAL_RUNTIME_TOKEN", "bad", "local_access_rejected"], ["API_BASE_URL", "https://evil.test", "local_access_rejected"],
] as const) {
    test(`rejects configuration ${key}:${value}`, async (t) => {
        const old = process.env[key];
        process.env[key] = value;
        t.after(() => { if (old === undefined) delete process.env[key]; else process.env[key] = old; });
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request(), context()), 403, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const headers of [{ Origin: "https://evil.test" }, { "Sec-Fetch-Site": "cross-site" }] as Record<string, string>[]) {
    test(`rejects cross-site request ${JSON.stringify(headers)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request(headers), context()), 403, "local_access_rejected");
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("rejects remote frontend host", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
    await error(await GET(request({}, undefined, "evil.test"), context()), 403, "local_access_rejected");
    assert.equal(mock.mock.callCount(), 0);
});
for (const id of ["bad", "A".repeat(32), "a".repeat(31), "a".repeat(33), "../path"]) {
    test(`rejects identifier ${id}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(DATA));
        await error(await GET(request(), context(id)), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}

// 缺字段和畸形响应不能被降级成未绑定；名称长度按 Unicode 码点验证。
for (const payload of [
    null, [], {}, { external_id: ID, name: "项目" }, { ...DATA, external_id: "c".repeat(32) },
    { ...DATA, name: "" }, { ...DATA, name: 123 }, { ...DATA, name: "😀".repeat(101) },
    ...["", "relative", 123, [], `${DATA.root_path}\0`].map((root_path) => ({ ...DATA, root_path })),
]) {
    test(`rejects invalid payload ${JSON.stringify(payload)}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(payload));
        await error(await GET(request(), context()), 502, "invalid_backend_response");
    });
}
test("accepts 100 Unicode code points", async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ ...DATA, name: "😀".repeat(100) }));
    assert.equal((await GET(request(), context())).status, 200);
});
for (const [status, code] of [
    [403, "local_mode_required"], [403, "local_access_rejected"], [404, "workspace_not_accessible"],
    [422, "invalid_workspace_input"], [500, "workspace_directory_read_failed"],
] as const) {
    test(`maps safe error ${status}:${code}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await error(await GET(request(), context()), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, code] of [
    [404, "invalid_workspace_input"], [422, "toString"], [422, "__proto__"],
    [418, "workspace_directory_read_failed"], [500, "workspace_binding_failed"], [500, 123],
] as const) {
    test(`rejects mismatched error ${status}:${code}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await error(await GET(request(), context()), 502, "invalid_backend_response");
    });
}
for (const kind of ["network", "json"]) {
    test(`handles ${kind} failure without retry`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (kind === "network") throw new TypeError("PRIVATE");
            return new Response("PRIVATE invalid JSON");
        });
        await error(await GET(request(), context()), 502, kind === "network" ? "workspace_directory_read_unavailable" : "invalid_backend_response");
        assert.equal(mock.mock.callCount(), 1);
    });
}

// 同时覆盖 fetch 和正文阶段，包含正文已返回但信号刚好中断的竞争情况。
for (const phase of ["before", "fetch", "response-json", "after-json"]) {
    for (const kind of phase === "before" ? ["client"] : ["client", "timeout"]) {
        test(`${kind} abort during ${phase}`, async (t) => {
            const client = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, "timeout", (ms: number) => { assert.equal(ms, 10_000); return timeout.signal; });
            const abort = () => (kind === "client" ? client : timeout).abort();
            if (phase === "before") abort();
            const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                const signal = init.signal as AbortSignal;
                if (phase === "fetch") { abort(); signal.throwIfAborted(); }
                const response = Response.json(DATA);
                if (phase === "response-json" || phase === "after-json") {
                    t.mock.method(response, "json", async () => {
                        abort();
                        if (phase === "response-json") signal.throwIfAborted();
                        return DATA;
                    });
                }
                return response;
            });
            await error(await GET(request({}, client.signal), context()), kind === "client" ? 499 : 504, `workspace_directory_read_${kind === "client" ? "cancelled" : "timeout"}`);
            assert.equal(mock.mock.callCount(), phase === "before" ? 0 : 1);
        });
    }
}
