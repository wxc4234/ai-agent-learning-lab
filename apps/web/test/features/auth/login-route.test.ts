import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000";
const { POST } = await import("../../../src/app/api/auth/login/route.ts");

const ORIGIN = "http://localhost:3000";
const PASSWORD = "BFF-Test-Password!";
const COOKIE = "agent_session=synthetic-token; Path=/; HttpOnly; Secure; SameSite=lax; Expires=Tue, 15 Sep 2026 08:00:00 GMT";
const SECOND_COOKIE = "another=value; Path=/; HttpOnly";

function request(headers: Record<string, string> = {}, body = JSON.stringify({
    username: " 中文AGENT ", password: PASSWORD,
}), signal?: AbortSignal): Request {
    return new Request(`${ORIGIN}/api/auth/login`, {
        method: "POST",
        headers: { Origin: ORIGIN, "Content-Type": "application/json", ...headers },
        body,
        signal,
    });
}

function success(): Response {
    const headers = new Headers();
    headers.append("Set-Cookie", COOKIE);
    headers.append("Set-Cookie", SECOND_COOKIE);
    return Response.json({ external_id: "user-id", username: "中文agent", ignored: PASSWORD }, { headers });
}

async function assertError(response: Response, status: number, code: string): Promise<void> {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    const body: unknown = await response.json();
    assert.equal(typeof body, "object");
    const error = body as { code: string; message: string };
    assert.deepEqual(Object.keys(error).sort(), ["code", "message"]);
    assert.equal(error.code, code);
    assert.equal(JSON.stringify(error).includes(PASSWORD), false);
}

test("forwards exact credentials, headers and safe identity with separate cookies", async (t) => {
    let received: RequestInit | undefined;
    let url: string | undefined;
    t.mock.method(globalThis, "fetch", async (input: string, init: RequestInit) => {
        url = input;
        received = init;
        return success();
    });
    const response = await POST(request({ "Content-Type": "application/json; charset=utf-8" }));
    assert.equal(response.status, 200);
    assert.equal(url, "http://backend.test/auth/login");
    assert.ok(received);
    assert.equal(received.method, "POST");
    const headers = new Headers(received.headers);
    assert.equal(headers.get("content-type"), "application/json");
    assert.equal(headers.get("origin"), ORIGIN);
    assert.equal(headers.get("cookie"), null);
    assert.deepEqual(JSON.parse(String(received.body)), { username: " 中文AGENT ", password: PASSWORD });
    assert.equal(received.cache, "no-store");
    assert.equal(received.redirect, "error");
    assert.ok(received.signal instanceof AbortSignal);
    assert.deepEqual(response.headers.getSetCookie(), [COOKIE, SECOND_COOKIE]);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), { external_id: "user-id", username: "中文agent" });
});

for (const origin of [null, "null", "https://evil.test", "http://localhost:3000.evil.test", "http://localhost:3001", "https://localhost:3000", "http://localhost:3000/"]) {
    test(`rejects origin ${origin}`, async (t) => {
        let calls = 0;
        t.mock.method(globalThis, "fetch", async () => { calls += 1; return success(); });
        const input = request();
        if (origin === null) input.headers.delete("origin");
        else input.headers.set("origin", origin);
        await assertError(await POST(input), 403, "login_origin_rejected");
        assert.equal(calls, 0);
    });
}

for (const contentType of [null, "text/plain", "application/x-www-form-urlencoded"]) {
    test(`rejects content type ${contentType}`, async (t) => {
        let calls = 0;
        t.mock.method(globalThis, "fetch", async () => { calls += 1; return success(); });
        const input = request();
        if (contentType === null) input.headers.delete("content-type");
        else input.headers.set("content-type", contentType);
        await assertError(await POST(input), 415, "unsupported_login_content_type");
        assert.equal(calls, 0);
    });
}

test("rejects malformed JSON before contacting backend", async (t) => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => { calls += 1; return success(); });
    await assertError(await POST(request({}, "{")), 400, "invalid_login_json");
    assert.equal(calls, 0);
});

for (const [status, code] of [
    [400, "request_rejected"], [401, "invalid_credentials"], [403, "login_origin_rejected"],
    [415, "unsupported_login_content_type"], [422, "invalid_login_input"], [500, "login_failed"],
] as const) {
    test(`preserves safe backend category ${status} and removes internal details`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: PASSWORD, detail: PASSWORD }, {
            status, headers: { "Set-Cookie": COOKIE },
        }));
        await assertError(await POST(request()), status, code);
    });
}

for (const [name, makeResponse, expectedCode] of [
    ["array", () => Response.json([]), "invalid_backend_response"],
    ["bad identity", () => Response.json({ external_id: 123, username: "test" }), "invalid_backend_response"],
    ["missing cookie", () => Response.json({ external_id: "id", username: "test" }), "invalid_backend_response"],
    ["unexpected status", () => Response.json({ code: "unknown" }, { status: 429 }), "invalid_backend_response"],
    ["wrong code", () => Response.json({ code: "unknown" }, { status: 401 }), "invalid_backend_response"],
    ["non JSON", () => new Response(PASSWORD, { status: 502 }), "login_backend_unavailable"],
] as const) {
    test(`safely rejects backend ${name}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => makeResponse());
        await assertError(await POST(request()), 502, expectedCode);
    });
}

test("connection failures do not expose error messages", async (t) => {
    t.mock.method(globalThis, "fetch", async () => { throw new Error(PASSWORD); });
    await assertError(await POST(request()), 502, "login_backend_unavailable");
});

for (const stage of ["fetch", "body"]) {
    test(`timeout covers ${stage}`, async (t) => {
        const timeout = new AbortController();
        t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
            assert.equal(milliseconds, 10_000);
            return timeout.signal;
        });
        t.mock.method(globalThis, "fetch", async (_input: string, init: RequestInit) => {
            const signal = init.signal;
            assert.ok(signal);
            const stalled = () => new Promise<never>((_resolve, reject) => {
                signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                queueMicrotask(() => timeout.abort(new DOMException("test timeout", "TimeoutError")));
            });
            if (stage === "fetch") return stalled();
            const response = success();
            t.mock.method(response, "json", stalled);
            return response;
        });
        await assertError(await POST(request()), 504, "login_backend_timeout");
    });
}

test("client cancellation propagates to backend", async (t) => {
    const client = new AbortController();
    t.mock.method(globalThis, "fetch", async (_input: string, init: RequestInit) => {
        const signal = init.signal;
        assert.ok(signal);
        return new Promise<never>((_resolve, reject) => {
            signal.addEventListener("abort", () => reject(signal.reason), { once: true });
            client.abort();
        });
    });
    await assertError(await POST(request({}, undefined, client.signal)), 499, "login_request_cancelled");
});
