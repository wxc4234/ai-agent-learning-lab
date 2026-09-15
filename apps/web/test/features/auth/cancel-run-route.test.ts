import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
const { POST } = await import("../../../src/app/api/runs/[runId]/cancel/route.ts");
const COOKIE = `agent_session=${"x".repeat(43)}`;
const context = (runId = "17") => ({ params: Promise.resolve({ runId }) });

function request(headers: Record<string, string> = {}, body = '{"reason":"user"}', signal?: AbortSignal) {
    return new Request("http://localhost:3000/api/runs/17/cancel", {
        method: "POST", signal, body,
        headers: { Origin: "http://localhost:3000", "Content-Type": "application/json", Cookie: COOKIE, ...headers },
    });
}

async function safeError(response: Response, status: number) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    assert.equal((await response.text()).includes("SECRET"), false);
}

for (const [headers, status] of [
    [{ Origin: "" }, 403], [{ Origin: "null" }, 403],
    [{ Origin: "http://localhost:3000.evil.test" }, 403],
    [{ "Content-Type": "text/plain" }, 415],
    [{ Cookie: "" }, 401], [{ Cookie: "agent_session=short" }, 401],
    [{ Cookie: `${COOKIE}; ${COOKIE}` }, 401],
    [{ Cookie: `agent_session=bad; ${COOKIE}` }, 401],
    [{ Cookie: `${COOKIE}; agent_session=bad` }, 401],
    [{ Cookie: `${COOKIE}x` }, 401],
] as [Record<string, string>, number][]) {
    test(`cancel rejects local boundary ${JSON.stringify(headers)}`, async (t) => {
        const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
        await safeError(await POST(request(headers), context()), status);
        assert.equal(fetch.mock.callCount(), 0);
    });
}

for (const runId of ["0", "-1", "1.2", "abc", "1/../2"]) {
    test(`cancel rejects invalid run id ${runId}`, async (t) => {
        const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
        await safeError(await POST(request(), context(runId)), 422);
        assert.equal(fetch.mock.callCount(), 0);
    });
}

test("cancel malformed JSON is rejected before fetch", async (t) => {
    const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
    await safeError(await POST(request({}, "{"), context()), 400);
    assert.equal(fetch.mock.callCount(), 0);
});

test("cancel forwards unique cookie, origin, body and signal and preserves empty 204", async (t) => {
    const input = request({ Cookie: `theme=dark; ${COOKIE}; tracking=SECRET` });
    t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(url, "http://backend.test/runs/17/cancel");
        assert.deepEqual([...new Headers(init.headers)], [
            ["content-type", "application/json"], ["cookie", COOKIE], ["origin", "http://localhost:3000"],
        ]);
        assert.equal(init.signal, input.signal);
        assert.equal(init.cache, "no-store");
        assert.equal(init.redirect, "error");
        assert.equal(init.method, "POST");
        assert.deepEqual(JSON.parse(init.body as string), { reason: "user" });
        return new Response(null, { status: 204, headers: { "Set-Cookie": "SECRET=secret" } });
    });
    const response = await POST(input, context());
    assert.equal(response.status, 204);
    assert.equal(response.body, null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
});

for (const status of [400, 401, 403, 404, 415, 422, 429, 500, 502, 503, 504, 200, 201, 302, 418]) {
    test(`cancel sanitizes upstream ${status} and discards body`, async (t) => {
        let cancelled = false;
        const body = new ReadableStream({ cancel() { cancelled = true; } });
        t.mock.method(globalThis, "fetch", async () => new Response(body, {
            status, headers: { "Set-Cookie": "SECRET=secret" },
        }));
        await safeError(await POST(request(), context()), [200, 201, 302, 418].includes(status) ? 502 : status);
        assert.equal(cancelled, true);
    });
}

for (const aborted of [false, true]) {
    test(`cancel network failure distinguishes aborted=${aborted}`, async (t) => {
        const controller = new AbortController();
        t.mock.method(globalThis, "fetch", async () => {
            if (aborted) controller.abort();
            throw new Error("SECRET");
        });
        await safeError(await POST(request({}, "{}", controller.signal), context()), aborted ? 499 : 502);
    });
}
