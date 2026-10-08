import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { previewCodeQueryContext, CODE_QUERY_BROWSER_BYTES, CODE_QUERY_BROWSER_TIMEOUT_MS } from "../../../src/features/workbench/code-query-context-request.ts";
import { codeContextDigest, type CodeQueryContext } from "../../../src/features/workbench/code-query-context-data.ts";
import type { CodeBatchSummary } from "../../../src/features/workbench/code-batch-summaries-data.ts";

const fixture = JSON.parse(readFileSync(new URL("./code-query-context.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; request: { query: string; batch_id: string; response_model: string };
    results: Record<string, CodeQueryContext>;
};
const { workspaceId, taskId } = fixture;
const snapshot = () => structuredClone(fixture.results.basic);
function batchFor(value: CodeQueryContext): CodeBatchSummary {
    const c = value.context, m = c.source_metadata;
    return { batch_id: c.batch_id, space_id: c.space_id, requested_model: m.requested_model,
        response_model: m.response_model, dimensions: c.dimensions, chunk_count: c.recall_summary.batch_chunk_count,
        truncated: m.truncated, incomplete_reasons: [...m.incomplete_reasons], created_at: "2026-10-08T00:00:00Z" };
}
const load = (signal = new AbortController().signal, batch = batchFor(snapshot()), query = fixture.request.query) =>
    previewCodeQueryContext(workspaceId, taskId, batch, query, signal);

for (const [name, value] of Object.entries(fixture.results)) {
    test(`explicit browser preview projects three fields and validates ${name}`, async t => {
        const signal = new AbortController().signal;
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `/api/workspaces/${workspaceId}/tasks/${taskId}/code-query-context`);
            assert.deepEqual(init, { method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify(fixture.request), signal });
            return Response.json(value);
        });
        const result = await load(signal, batchFor(value));
        assert.deepEqual(result, value); assert.notEqual(result, value); assert.equal(mock.mock.callCount(), 1);
        assert.equal(CODE_QUERY_BROWSER_TIMEOUT_MS, 75_000);
    });
}
for (const query of ["", " \n\u001c", "x\u0000", "x\ud800", "x".repeat(2001), "中".repeat(1366)]) {
    test(`invalid query fails before fetch (${query.length} UTF16 units)`, async t => {
        t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
        await assert.rejects(load(undefined, undefined, query), { message: "invalid_code_query_input" });
    });
}
for (const query of ["x".repeat(2000), "🚀".repeat(1024)]) {
    test(`exact query character/byte boundary preserves original input (${query.length})`, async t => {
        const value = snapshot(); value.query_sha256 = await codeContextDigest(query);
        t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(JSON.parse(String(init?.body)).query, query); return Response.json(value);
        });
        assert.deepEqual(await load(undefined, undefined, query), value);
    });
}
test("invalid target, batch and model are rejected before fetch", async t => {
    t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
    const signal = new AbortController().signal;
    for (const workspace of ["PRIVATE", workspaceId.toUpperCase()])
        await assert.rejects(previewCodeQueryContext(workspace, taskId, batchFor(snapshot()), fixture.request.query, signal));
    await assert.rejects(previewCodeQueryContext(workspaceId, taskId + "\n", batchFor(snapshot()), fixture.request.query, signal));
    for (const change of [{ batch_id: "PRIVATE" }, { response_model: " model " }])
        await assert.rejects(load(signal, { ...batchFor(snapshot()), ...change }));
});
test("already aborted preview never fetches", async t => {
    t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
    const controller = new AbortController(); controller.abort();
    await assert.rejects(load(controller.signal), { name: "AbortError" });
});
for (const status of [401, 404, 409, 422, 502, 503, 504]) {
    test(`HTTP ${status} is unknown and cancels unread error body`, async t => {
        let cancelled = false;
        const body = new ReadableStream<Uint8Array>({ pull() { assert.fail("must not read error"); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { status }));
        await assert.rejects(load(), { message: "code_query_response_failed" });
        assert.ok(cancelled); assert.equal(body.locked, false);
    });
}
for (const contentType of [null, "text/html"]) {
    test(`unsupported media ${contentType} is cancelled unread`, async t => {
        let cancelled = false;
        const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: contentType ? { "Content-Type": contentType } : {} }));
        await assert.rejects(load()); assert.ok(cancelled);
    });
}
for (const change of [{ space_id: "e".repeat(64) }, { dimensions: 4 }, { requested_model: "other-model" },
    { chunk_count: 3 }, { truncated: true, incomplete_reasons: ["file_budget"] }, { incomplete_reasons: ["chunk_budget"] }]) {
    test(`deeply valid result must still match chosen batch ${Object.keys(change)[0]}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json(snapshot()));
        await assert.rejects(load(undefined, { ...batchFor(snapshot()), ...change }), { message: "code_query_batch_mismatch" });
    });
}
for (const name of ["query", "batch", "task", "model", "text", "extra", "count"]) {
    test(`browser rejects public provenance inconsistency ${name}`, async t => {
        const value = snapshot();
        if (name === "query") value.query_sha256 = "e".repeat(64);
        if (name === "batch") value.context.batch_id = "e".repeat(32);
        if (name === "task") value.context.source_metadata.task_id = "e".repeat(32);
        if (name === "model") value.response_model = "other-version";
        if (name === "text") value.context.selected_chunks[0].chunk.text = "PRIVATE";
        if (name === "extra") Object.assign(value.context.source_metadata, { private_key: "PRIVATE" });
        if (name === "count") value.context.context_bytes++;
        t.mock.method(globalThis, "fetch", async () => Response.json(value));
        await assert.rejects(load(), { message: "invalid_code_query_snapshot" });
    });
}
for (const raw of ["{", "NaN", "\ufeff{}", '{"request_count":1,"request_co\\u0075nt":1}',
    JSON.stringify(fixture.results.basic).replace('"request_count":1', '"request_count":1.000000000000001')]) {
    test(`strict JSON refuses ambiguous/unusable public response ${raw.slice(0, 20)}`, async t => {
        const response = new Response(raw, { headers: { "Content-Type": "application/json" } });
        t.mock.method(globalThis, "fetch", async () => response);
        await assert.rejects(load()); assert.equal(response.body?.locked, false);
    });
}
for (const extra of [0, 1]) {
    test(`actual byte budget ${extra ? "rejects excess" : "accepts exact"} after browser decoding`, async t => {
        const json = JSON.stringify(snapshot());
        const bytes = new TextEncoder().encode(json).length;
        const response = new Response(json + " ".repeat(CODE_QUERY_BROWSER_BYTES - bytes + extra), {
            headers: { "Content-Type": "application/json", "Content-Length": "1", "Content-Encoding": "gzip" },
        });
        t.mock.method(globalThis, "fetch", async () => response);
        if (extra) await assert.rejects(load(), { message: "code_query_body_limit" });
        else assert.deepEqual(await load(), snapshot());
        assert.equal(response.body?.locked, false);
    });
}
test("split UTF8 stream is intact, invalid UTF8 is refused", async t => {
    const bytes = new TextEncoder().encode(JSON.stringify(snapshot()));
    const body = new ReadableStream<Uint8Array>({ start(controller) {
        for (let index = 0; index < bytes.length; index += 67) controller.enqueue(bytes.slice(index, index + 67));
        controller.close();
    } });
    const mock = t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
    assert.deepEqual(await load(), snapshot()); assert.equal(body.locked, false);
    mock.mock.mockImplementation(async () => new Response(new Uint8Array([0xff]), { headers: { "Content-Type": "application/json" } }));
    await assert.rejects(load());
});
test("cancelled late fetch cannot publish or consume its result", async t => {
    const controller = new AbortController(); let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", async () => { controller.abort(); return new Response(body); });
    await assert.rejects(load(controller.signal), { name: "AbortError" }); assert.ok(cancelled);
});
for (const reason of ["user", "timeout", "hanging-cancel"]) {
    test(`body waiting ${reason} aborts without waiting for cancel callback`, { timeout: 1500 }, async t => {
        const controller = new AbortController(); let cancelled = false;
        let started!: () => void;
        const reading = new Promise<void>(resolve => { started = resolve; });
        const body = new ReadableStream<Uint8Array>({ pull() { started(); }, cancel() {
            cancelled = true;
            if (reason === "hanging-cancel") return new Promise<void>(() => {});
        } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
        const pending = load(controller.signal); await reading;
        controller.abort(reason === "timeout" ? new DOMException("deadline", "TimeoutError") : undefined);
        await assert.rejects(pending, { name: reason === "timeout" ? "TimeoutError" : "AbortError" });
        assert.ok(cancelled); assert.equal(body.locked, false);
    });
}
test("cancel during async SHA256 validation discards the projected result", async t => {
    const controller = new AbortController();
    const original = crypto.subtle.digest.bind(crypto.subtle);
    t.mock.method(crypto.subtle, "digest", async (...args: Parameters<typeof original>) => {
        const result = await original(...args); controller.abort(); return result;
    });
    t.mock.method(globalThis, "fetch", async () => Response.json(snapshot()));
    await assert.rejects(load(controller.signal), { name: "AbortError" });
});
test("network and partial body failures never retry or retain locked body", async t => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error("PRIVATE"); });
    await assert.rejects(load()); assert.equal(mock.mock.callCount(), 1);
    const body = new ReadableStream<Uint8Array>({ pull(controller) { controller.error(new Error("PRIVATE")); } }, { highWaterMark: 0 });
    mock.mock.mockImplementation(async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
    await assert.rejects(load()); assert.equal(mock.mock.callCount(), 2); assert.equal(body.locked, false);
});
