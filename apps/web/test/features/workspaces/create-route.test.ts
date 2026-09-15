import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000";
const { POST } = await import("../../../src/app/api/workspaces/route.ts");
const TOKEN = "Ab_-" + "x".repeat(39);
const COOKIE = `agent_session=${TOKEN}`;
const RESULT = {
    external_id: "f79199c95b16421ab166eb405596ae9a",
    name: "我的项目",
    created_at: "2026-09-15T08:00:00.123456+00:00",
};

function request(body: unknown = { name: "  我的项目  " }, headers: Record<string, string | null> = {}, signal?: AbortSignal): Request {
    const values = new Headers({ Origin: "http://localhost:3000", "Content-Type": "application/json", Cookie: COOKIE });
    for (const [key, value] of Object.entries(headers)) {
        if (value === null) values.delete(key);
        else values.set(key, value);
    }
    return new Request("http://localhost:3000/api/workspaces?user_id=forged", {
        method: "POST", headers: values, body: JSON.stringify(body), signal,
    });
}

async function error(response: Response, status: number, code: string): Promise<void> {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(typeof body.message, "string");
    assert.equal(JSON.stringify(body).includes(TOKEN), false);
}

// 检查真实转发参数，防止额外 Cookie、身份头和字段越过边界。
for (const origin of ["http://localhost:3000", "http://127.0.0.1:3000"]) {
    test(`creates via allowed origin ${origin}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
            assert.equal(url, "http://backend.test/workspaces");
            assert.equal(init.method, "POST");
            assert.deepEqual(Object.fromEntries(new Headers(init.headers)), {
                "content-type": "application/json", cookie: COOKIE, origin,
            });
            assert.deepEqual(JSON.parse(String(init.body)), { name: "  我的项目  " });
            assert.equal(init.cache, "no-store");
            assert.equal(init.redirect, "error");
            assert.ok(init.signal instanceof AbortSignal);
            return Response.json({ ...RESULT, user_id: 42, token: TOKEN }, {
                status: 201, headers: { "Set-Cookie": COOKIE, "X-Internal": TOKEN },
            });
        });
        const response = await POST(request(undefined, {
            Origin: origin, Cookie: `theme=dark; ${COOKIE}; analytics=a=b`,
            "Content-Type": "application/json; charset=utf-8", "X-User-Id": "forged",
        }));
        assert.equal(response.status, 201);
        assert.deepEqual(await response.json(), RESULT);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.deepEqual(response.headers.getSetCookie(), []);
        assert.equal(response.headers.get("x-internal"), null);
        assert.equal(mock.mock.callCount(), 1);
    });
}

const rejectedHeaders: [string, Record<string, string | null>, number, string][] = [
    ...[null, "null", "https://evil.test", "http://localhost:3000.evil.test"].map((Origin): [string, Record<string, string | null>, number, string] => [
        `origin ${Origin}`, { Origin }, 403, "workspace_origin_rejected",
    ]),
    ...[null, "text/plain", "application/x-www-form-urlencoded"].map((value): [string, Record<string, string | null>, number, string] => [
        `content type ${value}`, { "Content-Type": value }, 415, "unsupported_workspace_content_type",
    ]),
    ...[null, "theme=dark", "agent_session=", "agent_session=bad", `${COOKIE}x`,
        `${COOKIE}; ${COOKIE}`, `agent_session=bad; ${COOKIE}`, `${COOKIE}; agent_session=bad`,
        `agent_session="${TOKEN}"`, `agent_session=%41${TOKEN.slice(1)}`,
    ].map((Cookie): [string, Record<string, string | null>, number, string] => [
        `cookie ${Cookie}`, { Cookie }, 401, "invalid_login_session",
    ]),
];
for (const [label, headers, status, code] of rejectedHeaders) {
    test(`rejects ${label} before forwarding`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(RESULT));
        await error(await POST(request(undefined, headers)), status, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const body of [null, [], "name", {}, { name: 1 }, { name: null }, { name: "ok", user_id: 1 }]) {
    test(`rejects input ${JSON.stringify(body)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(RESULT));
        await error(await POST(request(body)), 422, "invalid_workspace_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}

test("malformed request JSON is rejected without forwarding", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(RESULT));
    const req = request();
    t.mock.method(req, "json", async () => { throw new SyntaxError(TOKEN); });
    await error(await POST(req), 400, "invalid_workspace_request");
    assert.equal(mock.mock.callCount(), 0);
});

for (const [status, code] of [
    [400, "invalid_workspace_request"], [401, "invalid_login_session"],
    [403, "workspace_origin_rejected"], [415, "unsupported_workspace_content_type"],
    [422, "invalid_workspace_input"], [422, "invalid_workspace_name"],
    [500, "workspace_creation_failed"],
] as const) {
    test(`maps safe ${status} ${code}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: TOKEN, detail: TOKEN }, {
            status, headers: { "Set-Cookie": COOKIE },
        }));
        await error(await POST(request()), status, code);
    });
}

for (const [label, payload, status] of [
    ["null", null, 201], ["array", [], 201], ["missing fields", {}, 201],
    ["numeric id", { ...RESULT, external_id: 1 }, 201],
    ["invalid id", { ...RESULT, external_id: "bad" }, 201],
    ["empty name", { ...RESULT, name: "" }, 201],
    ["long name", { ...RESULT, name: "𠀀".repeat(101) }, 201],
    ["numeric name", { ...RESULT, name: 1 }, 201],
    ["invalid date", { ...RESULT, created_at: "bad" }, 201],
    ["numeric date", { ...RESULT, created_at: 1 }, 201],
    ["wrong success status", RESULT, 200],
    ["unknown status", { code: "workspace_creation_failed" }, 503],
    ["unknown code", { code: TOKEN }, 422],
    ["mismatched code", { code: "invalid_login_session" }, 500],
] as const) {
    test(`rejects upstream ${label}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(payload, { status }));
        await error(await POST(request()), 502, "invalid_backend_response");
    });
}

test("accepts 100 supplementary Unicode characters", async (t) => {
    const result = { ...RESULT, name: "𠀀".repeat(100) };
    t.mock.method(globalThis, "fetch", async () => Response.json(result, { status: 201 }));
    const response = await POST(request({ name: result.name }));
    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), result);
});

for (const stage of ["network", "JSON"]) {
    test(`sanitizes ${stage} failure without retry`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (stage === "network") throw new Error(TOKEN);
            return new Response(TOKEN, { status: 201 });
        });
        await error(await POST(request()), 502, stage === "network" ? "workspace_backend_unavailable" : "invalid_backend_response");
        assert.equal(mock.mock.callCount(), 1);
    });
}

// 在连接和读取正文两个阶段分别注入取消，验证组合信号和错误分类。
for (const cause of ["client", "timeout", "both"] as const) {
    for (const stage of ["fetch", "body"]) {
        test(`${cause} cancellation during ${stage}`, async (t) => {
            const client = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
                assert.equal(milliseconds, 10_000);
                return timeout.signal;
            });
            const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                const signal = init.signal;
                assert.ok(signal);
                const stalled = () => new Promise<never>((_resolve, reject) => {
                    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                    queueMicrotask(() => {
                        if (cause !== "timeout") client.abort();
                        if (cause !== "client") timeout.abort();
                    });
                });
                if (stage === "fetch") return stalled();
                const response = Response.json(RESULT, { status: 201 });
                t.mock.method(response, "json", stalled);
                return response;
            });
            await error(await POST(request(undefined, {}, client.signal)), cause === "timeout" ? 504 : 499,
                cause === "timeout" ? "workspace_backend_timeout" : "workspace_request_cancelled");
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}

test("cancellation while reading browser body avoids backend", async (t) => {
    const client = new AbortController();
    const req = request(undefined, {}, client.signal);
    t.mock.method(req, "json", async () => { client.abort(); throw new Error(TOKEN); });
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json(RESULT));
    await error(await POST(req), 499, "workspace_request_cancelled");
    assert.equal(mock.mock.callCount(), 0);
});
