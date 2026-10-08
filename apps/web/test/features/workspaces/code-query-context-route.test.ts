import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";
import { POST, runtime } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-query-context/route.ts";
import {
    codeQueryContextProxy, CODE_CONTEXT_REQUEST_BYTES, CODE_CONTEXT_RESPONSE_BYTES,
    CODE_CONTEXT_ERROR_BYTES, CODE_CONTEXT_TIMEOUT_MS,
} from "../../../src/app/api/_shared/code-query-context-proxy.ts";
import type { CodeQueryContext, CodeQueryContextRequest } from "../../../src/features/workbench/code-query-context-data.ts";

const fixture = JSON.parse(readFileSync(new URL("./code-query-context.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; request: CodeQueryContextRequest; results: Record<string, CodeQueryContext>;
};
const { workspaceId, taskId, request: body } = fixture;
const savedEnv = { ...process.env };
const origin = "http://localhost:3000";
const backend = "http://127.0.0.1:8000";
const token = "e".repeat(64);
const path = `/workspaces/${workspaceId}/tasks/${taskId}/code-query-context`;
const context = { params: Promise.resolve({ workspaceId, taskId }) };
const encoder = new TextEncoder();

function req(raw: unknown = body, init: RequestInit = {}): Request {
    return new Request(`${origin}/api${path}`, { method: "POST", body: JSON.stringify(raw),
        headers: { Origin: origin, "Content-Type": "application/json" }, ...init });
}
function payload(name = "basic"): CodeQueryContext { return structuredClone(fixture.results[name]); }
async function assertError(response: Response, status: number, code = "code_query_context_failed") {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("set-cookie"), null);
    const text = await response.text();
    assert.ok(!text.includes("PRIVATE") && !text.includes(token));
    const value = JSON.parse(text);
    assert.deepEqual(Object.keys(value).sort(), ["code", "message"]);
    assert.equal(value.code, code);
    assert.ok(typeof value.message === "string" && value.message.length > 0);
}
beforeEach(() => Object.assign(process.env, { APP_MODE: "local", API_BASE_URL: backend,
    LOCAL_RUNTIME_TOKEN: token, AUTH_ALLOWED_ORIGINS: origin }));
afterEach(() => { process.env = { ...savedEnv }; });

for (const name of Object.keys(fixture.results)) {
    test(`registered POST preserves public ${name} snapshot and only server credentials`, async t => {
        assert.equal(runtime, "nodejs");
        const snapshot = payload(name);
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `${backend}${path}`);
            assert.equal(init?.method, "POST");
            assert.deepEqual(Object.fromEntries(new Headers(init?.headers)), {
                "accept-encoding": "identity", "content-type": "application/json", origin, "x-local-runtime-token": token,
            });
            assert.deepEqual(JSON.parse(String(init?.body)), body);
            assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error");
            assert.ok(init?.signal instanceof AbortSignal);
            return Response.json(snapshot, { headers: { "Set-Cookie": "PRIVATE", "X-Provider": "PRIVATE", "Cache-Control": "public" } });
        });
        const response = await POST(req(body, { headers: { Origin: origin, "Content-Type": "application/json; charset=utf-8",
            Cookie: "PRIVATE", Authorization: "PRIVATE", "X-Local-Runtime-Token": "PRIVATE", "X-Forwarded-Host": "PRIVATE" } }), context);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), snapshot);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("set-cookie"), null); assert.equal(response.headers.get("x-provider"), null);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const field of ["user_id", "workspace_id", "task_id", "root", "base_url", "api_key", "config", "budget", "top_k", "transport"]) {
    test(`client cannot override ${field}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
        await assertError(await POST(req({ ...body, [field]: "PRIVATE" }), context), 422, "invalid_code_query_context_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const raw of [null, [], {}, { ...body, query: true }, { ...body, query: " " }, { ...body, query: "PRIVATE\u0000" },
    { ...body, query: "中".repeat(1366) }, { ...body, query: "x".repeat(2001) }, { ...body, query: "\ud800" },
    { ...body, response_model: " PRIVATE " }, { ...body, batch_id: "PRIVATE" }]) {
    test(`invalid body never forwards: ${JSON.stringify(raw).slice(0, 55)}`, async t => {
        t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
        await assertError(await POST(req(raw), context), 422, "invalid_code_query_context_input");
    });
}
for (const raw of ['{"query":', 'NaN', '{"query":"PRIVATE","query":"ok","batch_id":"' + body.batch_id + '","response_model":"fixture-model-v1"}']) {
    test(`malformed/duplicate request JSON does not forward: ${raw.slice(0, 40)}`, async t => {
        t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
        await assertError(await POST(req(undefined, { body: raw }), context), 422, "invalid_code_query_context_input");
    });
}
for (const [name, request, routeContext, status, code] of [
    ["query parameters", new Request(`${origin}/api${path}?user_id=PRIVATE`, { method: "POST", body: JSON.stringify(body), headers: { Origin: origin, "Content-Type": "application/json" } }), context, 422, "invalid_code_query_context_input"],
    ["path", req(), { params: Promise.resolve({ workspaceId: "PRIVATE", taskId }) }, 422, "invalid_code_query_context_input"],
    ["origin missing", req(body, { headers: { "Content-Type": "application/json" } }), context, 403, "workspace_origin_rejected"],
    ["origin mismatch", req(body, { headers: { Origin: "https://evil.test", "Content-Type": "application/json" } }), context, 403, "workspace_origin_rejected"],
    ["media", req(body, { headers: { Origin: origin, "Content-Type": "text/plain" } }), context, 415, "unsupported_workspace_content_type"],
    ["cross site", req(body, { headers: { Origin: origin, "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site" } }), context, 403, "local_access_rejected"],
] as const) {
    test(`preflight boundary rejects ${name}`, async t => {
        t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
        await assertError(await POST(request, routeContext), status, code);
    });
}
for (const [key, value, code] of [
    ["APP_MODE", "account", "local_mode_required"], ["APP_MODE", "PRIVATE", "local_access_rejected"],
    ["LOCAL_RUNTIME_TOKEN", "PRIVATE", "local_access_rejected"], ["AUTH_ALLOWED_ORIGINS", "http://elsewhere", "local_access_rejected"],
    ["API_BASE_URL", "https://127.0.0.1:8000", "local_access_rejected"], ["API_BASE_URL", "http://remote.test", "local_access_rejected"],
    ["API_BASE_URL", "http://PRIVATE@127.0.0.1:8000", "local_access_rejected"], ["API_BASE_URL", backend + "?PRIVATE", "local_access_rejected"],
    ["API_BASE_URL", backend + "#PRIVATE", "local_access_rejected"],
] as const) {
    test(`server configuration rejects ${key}=${value}`, async t => {
        process.env[key] = value;
        t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
        await assertError(await POST(req(), context), 403, code);
    });
}
test("remote browser origin cannot receive a server credential", async t => {
    process.env.AUTH_ALLOWED_ORIGINS = "http://remote.test";
    t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
    const request = new Request(`http://remote.test/api${path}`, { method: "POST", headers: { Origin: "http://remote.test", "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await assertError(await POST(request, context), 403, "local_access_rejected");
});
test("helper rejects other methods", async t => {
    t.mock.method(globalThis, "fetch", () => assert.fail("no request"));
    await assertError(await codeQueryContextProxy(new Request(`${origin}/api${path}`), workspaceId, taskId), 405, "code_query_context_method_not_allowed");
});

for (const [status, code] of [
    [401, "invalid_login_session"], [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"],
    [404, "workspace_not_accessible"], [409, "code_embedding_query_result_invalid"], [409, "code_embedding_batch_inconsistent"], [409, "code_context_budget_too_small"],
    [415, "unsupported_workspace_content_type"], [422, "invalid_code_query_context_input"], [422, "embedding_query_invalid"],
    [422, "embedding_query_budget_exceeded"], [422, "code_embedding_query_invalid"], [500, "code_query_context_failed"],
    [500, "code_embedding_distance_invalid"], [500, "code_context_budget_invalid"], [500, "code_context_query_result_invalid"],
    [500, "code_context_snapshot_invalid"], [500, "code_context_snapshot_too_large"], [502, "embedding_request_failed"],
    [502, "embedding_response_invalid"], [502, "embedding_response_too_large"], [502, "code_embedding_query_zero"],
    [503, "embedding_not_configured"], [503, "embedding_config_invalid"], [504, "embedding_timeout"],
] as const) {
    test(`known status/code pair uses a fixed public message: ${status}/${code}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE /private/key", api_key: "PRIVATE" }, { status }));
        await assertError(await POST(req(), context), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, code] of [[200, "workspace_not_accessible"], [404, "embedding_timeout"], [500, "PRIVATE"], [503, "code_context_budget_invalid"]] as const) {
    test(`wrong status/code pair cannot masquerade as known failure: ${status}/${code}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
        await assertError(await POST(req(), context), 502);
    });
}
for (const [name, response] of [
    ["null", () => Response.json(null)], ["array", () => Response.json([])],
    ["wrong batch", () => Response.json({ ...payload(), context: { ...payload().context, batch_id: "d".repeat(32) } })],
    ["unknown success field", () => Response.json({ ...payload(), api_key: "PRIVATE" })],
    ["bad JSON", () => new Response("PRIVATE", { headers: { "Content-Type": "application/json" } })],
    ["duplicate JSON", () => new Response(JSON.stringify(payload()).replace('{"query_sha256":', '{"query_sha256":"d' + 'd'.repeat(63) + '","query_sha256":'), { headers: { "Content-Type": "application/json" } })],
    ["invalid UTF8", () => new Response(new Uint8Array([0xc3, 0x28]), { headers: { "Content-Type": "application/json" } })],
    ["error array", () => Response.json([], { status: 404 })], ["error missing code", () => Response.json({ message: "PRIVATE" }, { status: 500 })],
] as const) {
    test(`unknown/malformed upstream result is not published: ${name}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => response());
        await assertError(await POST(req(), context), 502); assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [name, status, media, encoding] of [
    ["unknown status", 201, "application/json", "identity"], ["redirect", 302, "application/json", "identity"],
    ["HTML", 200, "text/html", "identity"], ["no media", 200, "", "identity"], ["compression", 200, "application/json", "gzip"],
] as const) {
    test(`unsupported ${name} cancels unused upstream body`, async t => {
        let cancelled = false;
        t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ start(c) { c.enqueue(encoder.encode("PRIVATE")); }, cancel() { cancelled = true; } }), { status, headers: { "Content-Type": media, "Content-Encoding": encoding } }));
        await assertError(await POST(req(), context), 502); assert.equal(cancelled, true);
    });
}
test("network failure does not retry or disclose the exception", async t => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("PRIVATE key /private/path"); });
    await assertError(await POST(req(), context), 502); assert.equal(mock.mock.callCount(), 1);
});

for (const side of ["request", "success", "error"] as const) {
    test(`actual ${side} stream limit is enforced regardless of declared size`, async t => {
        const maximum = side === "request" ? CODE_CONTEXT_REQUEST_BYTES : side === "success" ? CODE_CONTEXT_RESPONSE_BYTES : CODE_CONTEXT_ERROR_BYTES;
        let cancelled = false;
        const stream = new ReadableStream({ start(c) { c.enqueue(new Uint8Array(maximum + 1)); }, cancel() { cancelled = true; } });
        const mock = t.mock.method(globalThis, "fetch", async () => new Response(stream, { status: side === "error" ? 404 : 200, headers: { "Content-Type": "application/json", "Content-Length": "1" } }));
        const request = side === "request" ? req(body, { body: stream, duplex: "half" } as RequestInit) : req();
        await assertError(await POST(request, context), side === "request" ? 422 : 502, side === "request" ? "invalid_code_query_context_input" : undefined);
        assert.equal(cancelled, true); assert.equal(mock.mock.callCount(), side === "request" ? 0 : 1);
    });
}
for (const side of ["request", "success", "error"] as const) {
    test(`exact ${side} stream byte limit remains readable`, async t => {
        const maximum = side === "request" ? CODE_CONTEXT_REQUEST_BYTES : side === "success" ? CODE_CONTEXT_RESPONSE_BYTES : CODE_CONTEXT_ERROR_BYTES;
        const text = JSON.stringify(side === "request" ? body : side === "error" ? { code: "workspace_not_accessible" } : payload());
        const padded = text + " ".repeat(maximum - encoder.encode(text).length);
        t.mock.method(globalThis, "fetch", async () => side === "request" ? Response.json(payload()) : new Response(padded, { status: side === "error" ? 404 : 200, headers: { "Content-Type": "application/json" } }));
        const response = await POST(side === "request" ? req(body, { body: padded }) : req(), context);
        if (side === "error") await assertError(response, 404, "workspace_not_accessible");
        else { assert.equal(response.status, 200); assert.deepEqual(await response.json(), payload()); }
    });
}
test("UTF8 split between response stream pieces retains Python code point counts", async t => {
    const bytes = encoder.encode(JSON.stringify(payload()));
    const split = bytes.findIndex(byte => byte === 0xf0) + 1;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ start(c) { c.enqueue(bytes.slice(0, split)); c.enqueue(bytes.slice(split)); c.close(); } }), { headers: { "Content-Type": "application/json" } }));
    assert.deepEqual(await (await POST(req(), context)).json(), payload());
});

for (const phase of ["before", "request", "fetch", "success", "error", "timeout-request", "timeout-fetch", "timeout-success"] as const) {
    test(`abort covers ${phase}, cancels streams and prevents publication`, async t => {
        const controller = new AbortController();
        const timedOut = phase.startsWith("timeout");
        if (timedOut) t.mock.method(AbortSignal, "timeout", (milliseconds: number) => { assert.equal(milliseconds, CODE_CONTEXT_TIMEOUT_MS); return controller.signal; });
        let cancelled = false;
        const stream = () => new ReadableStream<Uint8Array>({ pull() { queueMicrotask(() => controller.abort()); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
        if (phase === "before") controller.abort();
        const request = req(body, { ...(timedOut ? {} : { signal: controller.signal }),
            ...(phase.endsWith("request") ? { body: stream(), duplex: "half" } : {}) } as RequestInit);
        const mock = t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.ok(init?.signal);
            if (phase.endsWith("fetch")) {
                const pending = new Promise<Response>((_resolve, reject) => { init.signal!.addEventListener("abort", () => reject(init.signal!.reason), { once: true }); });
                controller.abort(); assert.equal(init.signal.aborted, true); return pending;
            }
            return new Response(stream(), { status: phase === "error" ? 404 : 200, headers: { "Content-Type": "application/json" } });
        });
        await assertError(await POST(request, context), timedOut ? 504 : 499);
        const noFetch = phase === "before" || phase.endsWith("request");
        assert.equal(mock.mock.callCount(), noFetch ? 0 : 1);
        if (phase !== "before" && !phase.endsWith("fetch")) assert.equal(cancelled, true);
    });
}
test("body read failure remains a sanitized failure", async t => {
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ pull(c) { c.error(new Error("PRIVATE")); } }), { headers: { "Content-Type": "application/json" } }));
    await assertError(await POST(req(), context), 502);
});
test("late fetch resolution after abort cancels its body instead of publishing success", async t => {
    const controller = new AbortController();
    let cancelled = false;
    t.mock.method(globalThis, "fetch", async () => {
        controller.abort();
        return new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/json" } });
    });
    await assertError(await POST(req(body, { signal: controller.signal }), context), 499);
    assert.equal(cancelled, true);
});
test("abort during async provenance validation cannot publish an already-read result", async t => {
    const controller = new AbortController();
    const original = crypto.subtle.digest.bind(crypto.subtle);
    t.mock.method(crypto.subtle, "digest", async (...args: Parameters<typeof original>) => { controller.abort(); return original(...args); });
    t.mock.method(globalThis, "fetch", async () => Response.json(payload()));
    await assertError(await POST(req(body, { signal: controller.signal }), context), 499);
});
test("a never-settling cancel callback cannot hang an aborted body read", async t => {
    const controller = new AbortController();
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
        pull() { queueMicrotask(() => controller.abort()); },
        cancel() { cancelled = true; return new Promise<void>(() => {}); },
    }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", async () => new Response(stream, { headers: { "Content-Type": "application/json" } }));
    await assertError(await POST(req(body, { signal: controller.signal }), context), 499);
    assert.equal(cancelled, true); assert.equal(stream.locked, false);
});
