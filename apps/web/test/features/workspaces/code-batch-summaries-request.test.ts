import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { loadCodeBatchSummaries, CODE_BATCH_BROWSER_BYTES, CODE_BATCH_BROWSER_TIMEOUT_MS } from "../../../src/features/workbench/code-batch-summaries-request.ts";
import type { CodeBatchSummaries } from "../../../src/features/workbench/code-batch-summaries-data.ts";

const fixture = JSON.parse(readFileSync(new URL("./code-batch-summaries.fixture.json", import.meta.url), "utf8")) as {
    workspaceId: string; taskId: string; results: Record<string, CodeBatchSummaries>;
};
const { workspaceId, taskId } = fixture;
const encoder = new TextEncoder();
const load = (signal = new AbortController().signal) => loadCodeBatchSummaries(workspaceId, taskId, signal);
const snapshot = () => structuredClone(fixture.results.basic);

for (const [name, value] of Object.entries(fixture.results)) {
    test(`browser GET validates the public ${name} snapshot without server credentials`, async t => {
        const signal = new AbortController().signal;
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `/api/workspaces/${workspaceId}/tasks/${taskId}/code-embedding-batches`);
            assert.deepEqual(init, { method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error", signal });
            return Response.json(value);
        });
        const result = await load(signal);
        assert.deepEqual(result, value); assert.equal(mock.mock.callCount(), 1);
        assert.equal(CODE_BATCH_BROWSER_TIMEOUT_MS, 25_000);
    });
}
for (const [workspace, task] of [["PRIVATE", taskId], [workspaceId, taskId + "\n"], [workspaceId.toUpperCase(), taskId]]) {
    test(`invalid browser scope fails before fetch ${workspace.slice(0, 8)}/${task.slice(-8)}`, async t => {
        t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
        await assert.rejects(loadCodeBatchSummaries(workspace, task, new AbortController().signal));
    });
}
test("already aborted browser read does not fetch", async t => {
    t.mock.method(globalThis, "fetch", () => assert.fail("must not forward"));
    const controller = new AbortController(); controller.abort();
    await assert.rejects(load(controller.signal), { name: "AbortError" });
});
for (const status of [401, 404, 409, 500, 502]) {
    test(`browser failure ${status} cancels without reading or reflecting its private body`, async t => {
        let cancelled = false;
        const body = new ReadableStream<Uint8Array>({ pull() { assert.fail("error must not be read"); }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { status }));
        await assert.rejects(load(), { message: "code_batch_response_failed" });
        assert.equal(cancelled, true); assert.equal(body.locked, false);
    });
}
for (const contentType of ["text/html", null]) {
    test(`unsupported media ${contentType} is cancelled unread`, async t => {
        let cancelled = false;
        const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: contentType ? { "Content-Type": contentType } : {} }));
        await assert.rejects(load()); assert.equal(cancelled, true);
    });
}
for (const raw of ["{", "NaN", "\ufeff" + JSON.stringify(fixture.results.empty),
    JSON.stringify(fixture.results.empty).replace('"limit":20', '"limit":20.0000000000000001'),
    JSON.stringify(fixture.results.empty).replace('"limit":20', '"limit":20,"li\\u006dit":20')]) {
    test(`browser strict JSON rejects ${raw.slice(0, 25)}`, async t => {
        const response = new Response(raw, { headers: { "Content-Type": "application/json" } });
        t.mock.method(globalThis, "fetch", async () => response);
        await assert.rejects(load()); assert.equal(response.body?.locked, false);
    });
}
for (const changes of [{ workspace_id: "f".repeat(32) }, { private_key: "PRIVATE" }, { batches: [], has_more: true }]) {
    test(`browser refuses a mismatched public snapshot ${Object.keys(changes)[0]}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ ...snapshot(), ...changes }));
        await assert.rejects(load(), { message: "invalid_code_batch_snapshot" });
    });
}
for (const extra of [0, 1]) {
    test(`actual browser byte cap ${extra ? "rejects excess" : "accepts exact"} despite declared length`, async t => {
        const json = JSON.stringify(fixture.results.empty);
        const raw = json + " ".repeat(CODE_BATCH_BROWSER_BYTES - encoder.encode(json).length + extra);
        const response = new Response(raw, { headers: { "Content-Type": "application/json", "Content-Length": "1" } });
        t.mock.method(globalThis, "fetch", async () => response);
        if (extra) await assert.rejects(load(), { message: "code_batch_body_limit" });
        else assert.deepEqual(await load(), fixture.results.empty);
        assert.equal(response.body?.locked, false);
    });
}
test("browser UTF8 decoder refuses replacement characters", async t => {
    t.mock.method(globalThis, "fetch", async () => new Response(new Uint8Array([0xff]), { headers: { "Content-Type": "application/json" } }));
    await assert.rejects(load());
});
test("browser UTF8 split across stream pieces preserves Unicode models", async t => {
    const value = fixture.results.model_boundary;
    const bytes = encoder.encode(JSON.stringify(value));
    const body = new ReadableStream<Uint8Array>({ start(controller) {
        for (let i = 0; i < bytes.length; i += 137) controller.enqueue(bytes.slice(i, i + 137));
        controller.close();
    } });
    t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
    assert.deepEqual(await load(), value); assert.equal(body.locked, false);
});
test("late fetch after browser cancellation discards its body", async t => {
    const controller = new AbortController(); let cancelled = false;
    const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", async () => { controller.abort(); return new Response(body); });
    await assert.rejects(load(controller.signal), { name: "AbortError" }); assert.equal(cancelled, true);
});
for (const reason of ["user", "timeout", "never-settling-cancel"]) {
    test(`browser ${reason} interrupts body waiting and releases its lock`, { timeout: 1500 }, async t => {
        const controller = new AbortController(); let cancelled = false;
        let started!: () => void;
        const reading = new Promise<void>(resolve => { started = resolve; });
        const body = new ReadableStream<Uint8Array>({ pull() { started(); }, cancel() {
            cancelled = true;
            if (reason === "never-settling-cancel") return new Promise<void>(() => {});
        } }, { highWaterMark: 0 });
        t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
        const pending = load(controller.signal);
        await reading;
        controller.abort(reason === "timeout" ? new DOMException("deadline", "TimeoutError") : undefined);
        await assert.rejects(pending, { name: reason === "timeout" ? "TimeoutError" : "AbortError" });
        assert.equal(cancelled, true); assert.equal(body.locked, false);
    });
}
test("browser rechecks cancellation after synchronous public validation", async t => {
    const controller = new AbortController();
    const original = Date.prototype.setUTCFullYear;
    t.mock.method(Date.prototype, "setUTCFullYear", function(this: Date, ...args: Parameters<typeof original>) {
        const result = original.apply(this, args); controller.abort(); return result;
    });
    t.mock.method(globalThis, "fetch", async () => Response.json(snapshot()));
    await assert.rejects(load(controller.signal), { name: "AbortError" });
});
test("browser stream failure unlocks the reader", async t => {
    const body = new ReadableStream<Uint8Array>({ pull(controller) { controller.error(new Error("PRIVATE")); } }, { highWaterMark: 0 });
    t.mock.method(globalThis, "fetch", async () => new Response(body, { headers: { "Content-Type": "application/json" } }));
    await assert.rejects(load()); assert.equal(body.locked, false);
});
test("browser network failure never retries", async t => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error("PRIVATE"); });
    await assert.rejects(load()); assert.equal(mock.mock.callCount(), 1);
});
