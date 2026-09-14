import assert from "node:assert/strict";
import { test } from "node:test";

process.env.API_BASE_URL = "http://backend.test";
const { POST } = await import("../../../src/app/api/chat/stream/route.ts");
const COOKIE = `agent_session=${"x".repeat(43)}`;

function request(headers: Record<string, string> = {}, body = '{"session_id":"test","prompt":"你好"}', signal?: AbortSignal) {
    return new Request("http://localhost:3000/api/chat/stream", {
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
    test(`rejects local boundary ${JSON.stringify(headers)}`, async (t) => {
        const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
        await safeError(await POST(request(headers)), status);
        assert.equal(fetch.mock.callCount(), 0);
    });
}

test("invalid JSON does not reach backend", async (t) => {
    const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
    await safeError(await POST(request({}, "{")), 400);
    assert.equal(fetch.mock.callCount(), 0);
});

test("forwards only session cookie and preserves unconsumed stream and signal", async (t) => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
        start(value) { controller = value; },
        cancel() { cancelled = true; },
    });
    const upstream = new Response(stream, { headers: {
        "Content-Type": "application/x-ndjson; charset=utf-8", "X-Run-ID": "123",
        "Set-Cookie": "SECRET=secret", "X-Internal": "SECRET",
    } });
    const input = request({ Cookie: `theme=dark; ${COOKIE}; tracking=SECRET` });
    t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        assert.equal(url, "http://backend.test/chat/stream");
        assert.deepEqual([...new Headers(init.headers)], [
            ["content-type", "application/json"], ["cookie", COOKIE], ["origin", "http://localhost:3000"],
        ]);
        assert.equal(init.signal, input.signal);
        assert.equal(init.cache, "no-store");
        assert.equal(init.redirect, "error");
        assert.equal(init.method, "POST");
        assert.deepEqual(JSON.parse(init.body as string), { session_id: "test", prompt: "你好" });
        return upstream;
    });
    const response = await POST(input);
    assert.equal(response.body, upstream.body);
    assert.equal(upstream.bodyUsed, false);
    assert.equal(response.headers.get("x-run-id"), "123");
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(response.headers.getSetCookie(), []);
    assert.equal(response.headers.get("x-internal"), null);
    const reader = response.body!.getReader();
    for (const part of ['{"type":', '"RUN_FINISHED"}\n']) {
        controller.enqueue(new TextEncoder().encode(part));
        assert.equal(new TextDecoder().decode((await reader.read()).value), part);
    }
    await reader.cancel();
    assert.equal(cancelled, true);
});

for (const status of [400, 401, 403, 404, 415, 422, 429, 500, 502, 503, 504, 201, 302, 418]) {
    test(`sanitizes upstream ${status} and cancels its body`, async (t) => {
        let cancelled = false;
        const body = new ReadableStream({ cancel() { cancelled = true; } });
        t.mock.method(globalThis, "fetch", async () => new Response(body, {
            status, headers: { "Set-Cookie": "SECRET=secret" },
        }));
        await safeError(await POST(request()), [201, 302, 418].includes(status) ? 502 : status);
        assert.equal(cancelled, true);
    });
}

for (const runId of ["0", "-1", "1.2", "abc", "1\t2"]) {
    test(`rejects invalid run id ${runId}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => new Response("SECRET", {
            headers: { "Content-Type": "application/x-ndjson", "X-Run-ID": runId },
        }));
        await safeError(await POST(request()), 502);
    });
}

for (const response of [Response.json({ secret: "SECRET" }), new Response(null, { headers: { "Content-Type": "application/x-ndjson" } })]) {
    test("rejects successful response with invalid stream", async (t) => {
        t.mock.method(globalThis, "fetch", async () => response);
        await safeError(await POST(request()), 502);
    });
}

for (const aborted of [false, true]) {
    test(`fetch failure distinguishes cancellation ${aborted}`, async (t) => {
        const controller = new AbortController();
        t.mock.method(globalThis, "fetch", async () => {
            if (aborted) controller.abort();
            throw new Error("SECRET");
        });
        await safeError(await POST(request({}, "{}", controller.signal)), aborted ? 499 : 502);
    });
}
