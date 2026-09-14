import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000";
const { POST } = await import("../../../src/app/api/auth/logout/route.ts");
const ORIGIN = "http://localhost:3000";
const TOKEN = "a".repeat(43);
const COOKIE = `agent_session=${TOKEN}`;
const DELETION = 'agent_session=""; expires=Mon, 14 Sep 2026 00:00:00 GMT; HttpOnly; Max-Age=0; Path=/; SameSite=lax';

function request(cookie: string | null = COOKIE, signal?: AbortSignal): Request {
    const headers = new Headers({ Origin: ORIGIN });
    if (cookie !== null) headers.set("Cookie", cookie);
    return new Request(`${ORIGIN}/api/auth/logout`, { method: "POST", headers, signal });
}

function success(cookies: string[] = [DELETION]): Response {
    const headers = new Headers();
    for (const cookie of cookies) headers.append("Set-Cookie", cookie);
    return new Response(null, { status: 204, headers });
}

async function error(response: Response, status: number, code: string): Promise<void> {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(JSON.stringify(body).includes(TOKEN), false);
}

test("forwards only origin and login cookie; preserves separate deletion headers and empty 204", async (t) => {
    const cookies = [DELETION, "another=; Max-Age=0; Path=/"];
    t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(url, "http://backend.test/auth/logout");
        assert.equal(init.method, "POST");
        assert.deepEqual([...new Headers(init.headers)], [["cookie", COOKIE], ["origin", ORIGIN]]);
        assert.equal(init.body, undefined);
        assert.equal(init.cache, "no-store");
        assert.equal(init.redirect, "error");
        assert.ok(init.signal instanceof AbortSignal);
        const response = success(cookies);
        t.mock.method(response, "json", () => { throw new Error("204 must not be parsed"); });
        return response;
    });
    const response = await POST(request(`theme=dark; ${COOKIE}; analytics=a=b`));
    assert.equal(response.status, 204);
    assert.equal(await response.text(), "");
    assert.equal(response.headers.get("content-type"), null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), cookies);
});

for (const origin of [null, "null", "https://evil.test", `${ORIGIN}.evil.test`, `${ORIGIN}/`, "https://localhost:3000"]) {
    test(`rejects origin ${origin}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => success());
        const input = request();
        if (origin === null) input.headers.delete("origin");
        else input.headers.set("origin", origin);
        await error(await POST(input), 403, "logout_origin_rejected");
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const cookie of [null, "", "theme=dark", "agent_session=", "agent_session=short", `${COOKIE}x`, `agent_session="${TOKEN}"`, `agent_session=%61${TOKEN.slice(1)}`, `${COOKIE} ; theme=x`]) {
    test("missing or malformed single cookie still requests backend deletion", async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
            assert.equal(new Headers(init.headers).get("cookie"), null);
            return success();
        });
        const response = await POST(request(cookie));
        assert.equal(response.status, 204);
        assert.deepEqual(response.headers.getSetCookie(), [DELETION]);
        assert.equal(mock.mock.callCount(), 1);
    });
}

for (const cookie of [`${COOKIE}; ${COOKIE}`, `agent_session=bad; ${COOKIE}`, `${COOKIE}; agent_session=`, "agent_session=; agent_session=bad"]) {
    test("duplicate cookie fails without choosing a token or clearing cookie", async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => success());
        await error(await POST(request(cookie)), 400, "ambiguous_login_cookie");
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const cookies of [[], ["other=; Max-Age=0; Path=/"], ["agent_session=; Path=/"], ["agent_session=; Max-Age=1; Path=/"], ["agent_session=; Max-Age=0; Path=/auth"], ["agent_session=; Max-Age=0; Path=/; Domain=localhost"]]) {
    test("204 requires expected login cookie deletion", async (t) => {
        t.mock.method(globalThis, "fetch", async () => success(cookies));
        await error(await POST(request()), 502, "invalid_backend_response");
    });
}

for (const [status, code] of [[403, "logout_origin_rejected"], [500, "logout_failed"]] as const) {
    test(`safe backend ${status} never clears cookie`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: TOKEN, detail: TOKEN }, {
            status, headers: { "Set-Cookie": DELETION },
        }));
        await error(await POST(request()), status, code);
    });
}

for (const [body, status] of [[null, 500], [[], 500], [{ code: "wrong" }, 500], [{ code: "logout_failed" }, 200], [{ code: "logout_failed" }, 401]] as const) {
    test("unexpected backend payload or status is sanitized", async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(body, { status }));
        await error(await POST(request()), 502, "invalid_backend_response");
    });
}

for (const stage of ["connection", "JSON"]) {
    test(`backend ${stage} failure does not retry`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (stage === "connection") throw new Error(TOKEN);
            return new Response(TOKEN, { status: 500 });
        });
        await error(await POST(request()), 502, "logout_backend_unavailable");
        assert.equal(mock.mock.callCount(), 1);
    });
}

for (const cause of ["timeout", "client"]) {
    for (const stage of ["fetch", "body"]) {
        test(`${cause} during ${stage} propagates without cookie deletion`, async (t) => {
            const timeout = new AbortController();
            const client = new AbortController();
            t.mock.method(AbortSignal, "timeout", (ms: number) => {
                assert.equal(ms, 10_000);
                return timeout.signal;
            });
            t.mock.method(globalThis, "fetch", async (_url: string, init: RequestInit) => {
                const signal = init.signal;
                assert.ok(signal);
                const stalled = () => new Promise<never>((_resolve, reject) => {
                    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                    queueMicrotask(() => (cause === "timeout" ? timeout : client).abort());
                });
                if (stage === "fetch") return stalled();
                const response = Response.json({ code: "logout_failed" }, { status: 500 });
                t.mock.method(response, "json", stalled);
                return response;
            });
            await error(await POST(request(COOKIE, client.signal)), cause === "timeout" ? 504 : 499,
                cause === "timeout" ? "logout_backend_timeout" : "logout_request_cancelled");
        });
    }
}
