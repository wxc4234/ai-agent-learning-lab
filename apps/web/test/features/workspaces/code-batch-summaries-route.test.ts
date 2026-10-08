import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";
import { GET, runtime } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/code-embedding-batches/route.ts";
import { codeBatchSummariesProxy, CODE_BATCH_RESPONSE_BYTES, CODE_BATCH_ERROR_BYTES, CODE_BATCH_TIMEOUT_MS } from "../../../src/app/api/_shared/code-batch-summaries-proxy.ts";
import type { CodeBatchSummaries } from "../../../src/features/workbench/code-batch-summaries-data.ts";

const fixture = JSON.parse(readFileSync(new URL("./code-batch-summaries.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; results: Record<string, CodeBatchSummaries>;
};
const { workspaceId, taskId } = fixture;
const origin = "http://localhost:3000";
const backend = "http://127.0.0.1:8000";
const token = "e".repeat(64);
const path = `/workspaces/${workspaceId}/tasks/${taskId}/code-embedding-batches`;
const context = { params: Promise.resolve({ workspaceId, taskId }) };
const savedEnv = { ...process.env };
const encoder = new TextEncoder();
function req(init: RequestInit = {}, suffix = ""): Request { return new Request(`${origin}/api${path}${suffix}`, init); }
function snapshot(name = "basic"): CodeBatchSummaries { return structuredClone(fixture.results[name]); }
async function failure(response: Response, status: number, code = "code_embedding_batch_list_failed") {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("set-cookie"), null);
    const raw = await response.text();
    assert.ok(!raw.includes("PRIVATE") && !raw.includes(token));
    const value = JSON.parse(raw);
    assert.deepEqual(Object.keys(value).sort(), ["code", "message"]); assert.equal(value.code, code);
    assert.ok(typeof value.message === "string" && value.message.length > 0);
}
beforeEach(() => Object.assign(process.env, { APP_MODE: "local", API_BASE_URL: backend, LOCAL_RUNTIME_TOKEN: token, AUTH_ALLOWED_ORIGINS: origin }));
afterEach(() => { process.env = { ...savedEnv }; });

for (const name of Object.keys(fixture.results)) {
    test(`registered GET projects ${name}, drops caller secrets and upstream headers`, async t => {
        assert.equal(runtime, "nodejs");
        const value = snapshot(name);
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `${backend}${path}`); assert.equal(init?.method, "GET");
            assert.equal(init?.body, undefined); assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error");
            assert.ok(init?.signal instanceof AbortSignal);
            assert.deepEqual(Object.fromEntries(new Headers(init?.headers)), {
                "accept-encoding": "identity", origin, "x-local-runtime-token": token,
            });
            return Response.json(value, { headers: { "Set-Cookie": "PRIVATE", "X-Provider": "PRIVATE", "Cache-Control": "public" } });
        });
        const response = await GET(req({ headers: { Cookie: "PRIVATE", Authorization: "PRIVATE", "X-Local-Runtime-Token": "PRIVATE", "X-Forwarded-Host": "PRIVATE" } }), context);
        assert.equal(response.status, 200); assert.deepEqual(await response.json(), value);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("set-cookie"), null); assert.equal(response.headers.get("x-provider"), null);
        assert.equal(mock.mock.callCount(), 1);
    });
}
const ordinaryHeaders: Record<string, string>[] = [{ Origin: origin }, { "Content-Length": "0" }, { "Content-Type": "text/plain" }];
for (const headers of ordinaryHeaders) {
    test(`empty GET accepts ordinary headers ${JSON.stringify(headers)}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json(snapshot("empty")));
        const response = await GET(req({ headers }), context);
        assert.equal(response.status, 200); assert.deepEqual(await response.json(), snapshot("empty"));
    });
}
test("server path prefix is retained without accepting client URL overrides", async t => {
    process.env.API_BASE_URL = backend + "/internal";
    t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0]) => {
        assert.equal(String(url), `${backend}/internal${path}`); return Response.json(snapshot());
    });
    assert.equal((await GET(req(), context)).status, 200);
});
for (const [name, request, ctx, status, code] of [
    ["query", req({}, "?limit=1&limit=20"), context, 422, "invalid_code_embedding_batch_list_input"],
    ["identity query", req({}, "?user_id=PRIVATE"), context, 422, "invalid_code_embedding_batch_list_input"],
    ["bad workspace", req(), { params: Promise.resolve({ workspaceId: "PRIVATE", taskId }) }, 422, "invalid_code_embedding_batch_list_input"],
    ["bad task", req(), { params: Promise.resolve({ workspaceId, taskId: taskId + "\n" }) }, 422, "invalid_code_embedding_batch_list_input"],
    ["wrong origin", req({ headers: { Origin: "https://evil.test" } }), context, 403, "workspace_origin_rejected"],
    ["cross site", req({ headers: { "Sec-Fetch-Site": "cross-site" } }), context, 403, "local_access_rejected"],
    ["declared body", req({ headers: { "Content-Length": "1" } }), context, 422, "invalid_code_embedding_batch_list_input"],
    ["bad length", req({ headers: { "Content-Length": "PRIVATE" } }), context, 422, "invalid_code_embedding_batch_list_input"],
    ["transfer encoding", req({ headers: { "Transfer-Encoding": "chunked" } }), context, 422, "invalid_code_embedding_batch_list_input"],
] as const) {
    test(`preflight rejects ${name} before fetch`, async t => {
        const mock = t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
        await failure(await GET(request, ctx), status, code); assert.equal(mock.mock.callCount(), 0);
    });
}
test("unsupported GET body stream is cancelled without reading it", async t => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ pull() { assert.fail("body must not be read"); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
    const request = req();
    // 原生Request构造器禁止GET正文，显式模拟传输适配器交来的非空流。
    Object.defineProperty(request, "body", { value: body });
    t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
    await failure(await GET(request, context), 422, "invalid_code_embedding_batch_list_input");
    assert.equal(cancelled, true); assert.equal(body.locked, false);
});
test("helper rejects an unsupported method and releases its body", async t => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
    await failure(await codeBatchSummariesProxy(req({ method: "POST", body, duplex: "half" } as RequestInit), workspaceId, taskId), 405, "code_embedding_batch_list_method_not_allowed");
    assert.equal(cancelled, true);
});
for (const [key, value, code] of [
    ["APP_MODE", "account", "local_mode_required"], ["APP_MODE", "PRIVATE", "local_access_rejected"],
    ["LOCAL_RUNTIME_TOKEN", "PRIVATE", "local_access_rejected"], ["LOCAL_RUNTIME_TOKEN", "", "local_access_rejected"],
    ["AUTH_ALLOWED_ORIGINS", "http://elsewhere", "local_access_rejected"],
    ["API_BASE_URL", "https://127.0.0.1:8000", "local_access_rejected"], ["API_BASE_URL", "http://remote.test", "local_access_rejected"],
    ["API_BASE_URL", "http://PRIVATE@127.0.0.1:8000", "local_access_rejected"],
    ["API_BASE_URL", backend + "?PRIVATE", "local_access_rejected"], ["API_BASE_URL", backend + "#PRIVATE", "local_access_rejected"],
] as const) {
    test(`configuration rejects ${key}=${value}`, async t => {
        process.env[key] = value; t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
        await failure(await GET(req(), context), 403, code);
    });
}
const known = [
    [401, "invalid_login_session"], [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"],
    [404, "workspace_not_accessible"], [409, "code_embedding_project_unbound"], [422, "invalid_code_embedding_batch_list_input"], [500, "code_embedding_batch_list_failed"],
] as const;
for (const [status, code] of known) {
    test(`known ${status}/${code} uses fixed message and ignores raw errors`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE", private_key: "PRIVATE" }, { status }));
        await failure(await GET(req(), context), status, code);
    });
}
for (const [name, status, raw] of [
    ["unknown code", 404, { code: "PRIVATE" }], ["status mismatch", 500, { code: "workspace_not_accessible" }],
    ["array", 404, [{ code: "workspace_not_accessible" }]], ["missing code", 404, { message: "PRIVATE" }],
    ["not an error", 404, null], ["wrong code type", 404, { code: 404 }],
    ["extra success field", 200, { ...snapshot(), private_root: "PRIVATE" }],
    ["wrong resource", 200, { ...snapshot(), task_id: "c".repeat(32) }],
    ["false empty", 200, { ...snapshot("empty"), has_more: true }],
] as const) {
    test(`unknown or mismatched response rejects ${name}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json(raw, { status }));
        await failure(await GET(req(), context), 502);
    });
}
for (const raw of [
    "{", "NaN", JSON.stringify(snapshot()).replace('"limit":20', '"limit":20.0000000000000001'),
    JSON.stringify(snapshot()).replace('"limit":20', '"limit":20,"li\\u006dit":20'),
    JSON.stringify(snapshot()).replace('"requested_model":"fixture-model"', '"requested_model":"\\ud800"'),
    "\ufeff" + JSON.stringify(snapshot()),
]) {
    test(`strict upstream JSON rejects ${raw.slice(0, 65)}`, async t => {
        t.mock.method(globalThis, "fetch", async () => new Response(raw, { headers: { "Content-Type": "application/json" } }));
        await failure(await GET(req(), context), 502);
    });
}
for (const [name, status, media, encoding] of [
    ["redirect", 302, "application/json", "identity"], ["unexpected status", 503, "application/json", "identity"],
    ["HTML", 200, "text/html", "identity"], ["missing media", 200, "", "identity"], ["compressed", 200, "application/json", "gzip"],
] as const) {
    test(`unsupported ${name} cancels response without parsing`, async t => {
        let cancelled = false;
        const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { status, headers: { "Content-Type": media, "Content-Encoding": encoding } }));
        await failure(await GET(req(), context), 502); assert.equal(cancelled, true);
    });
}
for (const side of ["success", "error"] as const) {
    test(`actual ${side} byte cap ignores declared length`, async t => {
        let cancelled = false;
        const maximum = side === "success" ? CODE_BATCH_RESPONSE_BYTES : CODE_BATCH_ERROR_BYTES;
        const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new Uint8Array(maximum + 1)); }, cancel() { cancelled = true; } });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { status: side === "success" ? 200 : 404, headers: { "Content-Type": "application/json", "Content-Length": "1" } }));
        await failure(await GET(req(), context), 502); assert.equal(cancelled, true); assert.equal(body.locked, false);
    });
    test(`exact ${side} byte cap is accepted`, async t => {
        const maximum = side === "success" ? CODE_BATCH_RESPONSE_BYTES : CODE_BATCH_ERROR_BYTES;
        const raw = JSON.stringify(side === "success" ? snapshot() : { code: "workspace_not_accessible" });
        const padded = raw + " ".repeat(maximum - encoder.encode(raw).length);
        t.mock.method(globalThis, "fetch", async () => new Response(padded, { status: side === "success" ? 200 : 404, headers: { "Content-Type": "application/json; charset=utf-8" } }));
        const response = await GET(req(), context);
        if (side === "error") await failure(response, 404, "workspace_not_accessible");
        else { assert.equal(response.status, 200); assert.deepEqual(await response.json(), snapshot()); }
    });
}
test("invalid UTF8 cannot be replaced and published", async t => {
    t.mock.method(globalThis, "fetch", async () => new Response(new Uint8Array([0xff]), { headers: { "Content-Type": "application/json" } }));
    await failure(await GET(req(), context), 502);
});
test("UTF8 split across pieces preserves full model names", async t => {
    const value = snapshot("model_boundary"); const bytes = encoder.encode(JSON.stringify(value));
    const index = bytes.findIndex(byte => byte === 0xf0) + 1;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ start(c) { c.enqueue(bytes.slice(0, index)); c.enqueue(bytes.slice(index)); c.close(); } }), { headers: { "Content-Type": "application/json" } }));
    assert.deepEqual(await (await GET(req(), context)).json(), value);
});
for (const phase of ["before", "fetch", "success", "error", "timeout-fetch", "timeout-success", "timeout-error"] as const) {
    test(`abort and deadline cover ${phase}`, async t => {
        const controller = new AbortController(); const timeout = phase.startsWith("timeout");
        if (timeout) t.mock.method(AbortSignal, "timeout", (milliseconds: number) => { assert.equal(milliseconds, CODE_BATCH_TIMEOUT_MS); return controller.signal; });
        if (phase === "before") controller.abort();
        let cancelled = false;
        const mock = t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.ok(init?.signal);
            if (phase.endsWith("fetch")) {
                const pending = new Promise<Response>((_resolve, reject) => init.signal!.addEventListener("abort", () => reject(init.signal!.reason), { once: true }));
                controller.abort(); assert.equal(init.signal.aborted, true); return pending;
            }
            const body = new ReadableStream<Uint8Array>({ pull() { queueMicrotask(() => controller.abort()); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
            return new Response(body, { status: phase.endsWith("error") ? 404 : 200, headers: { "Content-Type": "application/json" } });
        });
        await failure(await GET(req(timeout ? {} : { signal: controller.signal }), context), timeout ? 504 : 499);
        assert.equal(mock.mock.callCount(), phase === "before" ? 0 : 1);
        if (phase !== "before" && !phase.endsWith("fetch")) assert.equal(cancelled, true);
    });
}
test("late fetch success after abort is cancelled rather than published", async t => {
    const controller = new AbortController(); let cancelled = false;
    t.mock.method(globalThis, "fetch", async () => {
        controller.abort(); return new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/json" } });
    });
    await failure(await GET(req({ signal: controller.signal }), context), 499); assert.equal(cancelled, true);
});
test("never-settling cancel callback cannot hang an aborted read", async t => {
    const controller = new AbortController(); let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ pull() { queueMicrotask(() => controller.abort()); }, cancel() { cancelled = true; return new Promise<void>(() => {}); } }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
    await failure(await GET(req({ signal: controller.signal }), context), 499);
    assert.equal(cancelled, true); assert.equal(body.locked, false);
});
test("stream read failure is sanitized", async t => {
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({ pull(c) { c.error(new Error("PRIVATE")); } }), { headers: { "Content-Type": "application/json" } }));
    await failure(await GET(req(), context), 502);
});
test("network failure is never retried", async t => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error("PRIVATE Key path"); });
    await failure(await GET(req(), context), 502); assert.equal(mock.mock.callCount(), 1);
});
