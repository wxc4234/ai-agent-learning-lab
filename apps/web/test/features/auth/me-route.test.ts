import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
const { GET } = await import("../../../src/app/api/auth/me/route.ts");
const TOKEN = "Ab_-" + "x".repeat(39);
const COOKIE = `agent_session=${TOKEN}`;
const IDENTITY = { external_id: "user-id", username: "中文agent" };

function request(cookie: string | null = COOKIE, signal?: AbortSignal): Request {
    return new Request("http://localhost:3000/api/auth/me?external_id=forged", {
        headers: cookie === null ? {} : { Cookie: cookie }, signal,
    });
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

for (const [name, cookie] of [
    ["missing", null], ["empty", ""], ["other", "theme=dark"],
    ["empty value", "agent_session="], ["short", "agent_session=abc"],
    ["long", `${COOKIE}x`], ["quoted", `agent_session="${TOKEN}"`],
    ["encoded", `agent_session=%41${TOKEN.slice(1)}`],
    ["equals", `${COOKIE}=`], ["leading value space", `agent_session= ${TOKEN}`],
    ["trailing value space", `${COOKIE} ; theme=dark`],
    ["duplicate", `${COOKIE}; ${COOKIE}`],
    ["invalid then valid", `agent_session=bad; ${COOKIE}`],
    ["valid then invalid", `${COOKIE}; agent_session=bad`],
    ["wrong case", `Agent_session=${TOKEN}`],
    ["name whitespace", `agent_session =${TOKEN}`],
] as const) {
    test(`rejects ${name} cookie without backend call`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(IDENTITY));
        await error(await GET(request(cookie)), 401, "invalid_login_session");
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const cookie of [COOKIE, `theme=dark; ${COOKIE}; analytics=a=b`, `broken;\t${COOKIE};`]) {
    test(`forwards only authentication cookie (${cookie === COOKIE ? "single" : "mixed"})`, async (t) => {
        t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
            assert.equal(url, "http://backend.test/auth/me");
            assert.equal(init.method, "GET");
            assert.deepEqual([...new Headers(init.headers)], [["cookie", COOKIE]]);
            assert.equal(init.body, undefined);
            assert.equal(init.cache, "no-store");
            assert.equal(init.redirect, "error");
            assert.ok(init.signal instanceof AbortSignal);
            return Response.json({ ...IDENTITY, token: TOKEN, password_hash: TOKEN }, {
                headers: { "Set-Cookie": `${COOKIE}; HttpOnly` },
            });
        });
        const response = await GET(request(cookie));
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), IDENTITY);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.deepEqual(response.headers.getSetCookie(), []);
    });
}

for (const [status, code] of [[401, "invalid_login_session"], [500, "current_user_failed"]] as const) {
    test(`preserves safe ${status} category`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: TOKEN, detail: TOKEN }, {
            status, headers: { "Set-Cookie": `${COOKIE}; Max-Age=0` },
        }));
        await error(await GET(request()), status, code);
    });
}

for (const [name, body, status] of [
    ["null", null, 200], ["array", [], 200], ["string", "internal", 200],
    ["missing identity", {}, 200], ["wrong id", { ...IDENTITY, external_id: 1 }, 200],
    ["wrong username", { ...IDENTITY, username: null }, 200],
    ["wrong error", { code: "unknown" }, 401],
    ["unexpected status", { code: "current_user_failed" }, 503],
] as const) {
    test(`rejects upstream ${name}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(body, { status }));
        await error(await GET(request()), 502, "invalid_backend_response");
    });
}

for (const stage of ["connection", "invalid JSON"]) {
    test(`sanitizes ${stage} failure`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => {
            if (stage === "connection") throw new Error(TOKEN);
            return new Response(TOKEN);
        });
        await error(await GET(request()), 502, "current_user_backend_unavailable");
    });
}

for (const cause of ["timeout", "client"] as const) {
    for (const stage of ["fetch", "body"]) {
        test(`${cause} abort covers ${stage}`, async (t) => {
            const timeout = new AbortController();
            const client = new AbortController();
            t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
                assert.equal(milliseconds, 10_000);
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
                const response = Response.json(IDENTITY);
                t.mock.method(response, "json", stalled);
                return response;
            });
            await error(await GET(request(COOKIE, client.signal)), cause === "timeout" ? 504 : 499,
                cause === "timeout" ? "current_user_backend_timeout" : "current_user_request_cancelled");
        });
    }
}
