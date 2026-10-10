import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { modelSettingsProxy } from "../../../src/app/api/_shared/model-settings-proxy.ts";
import { readSettings } from "../../../src/features/model-settings/data.ts";
import { parseAgentStreamLine } from "../../../src/features/chat/agent-stream.ts";

const saved = { ...process.env }, origin = "http://localhost:3000";
const channel = { enabled: true, base_url: "https://fixture.invalid", model: "fixture", dimensions: null, request_dimensions: false, key_configured: true, source: "local" };
const data = () => ({ revision: "a".repeat(64), chat: { ...channel }, embedding: { ...channel, dimensions: 3 } });
beforeEach(() => Object.assign(process.env, { APP_MODE: "local", API_BASE_URL: "http://127.0.0.1:8000", LOCAL_RUNTIME_TOKEN: "b".repeat(64), AUTH_ALLOWED_ORIGINS: origin }));
afterEach(() => { process.env = { ...saved }; });

test("response rejects secret or extra fields", () => {
    assert.ok(readSettings(data()));
    assert.equal(readSettings({ ...data(), api_key: "PRIVATE" }), null);
    assert.equal(readSettings({ ...data(), chat: { ...channel, api_key: "PRIVATE" } }), null);
});
test("BFF sends server token and preserves public response", async t => {
    t.mock.method(globalThis, "fetch", async (_url, init) => {
        assert.equal(new Headers(init?.headers).get("X-Local-Runtime-Token"), "b".repeat(64));
        assert.equal(new Headers(init?.headers).get("Authorization"), null);
        assert.equal(init?.redirect, "error");
        return Response.json(data());
    });
    const result = await modelSettingsProxy(new Request(origin + "/api/model-settings", { headers: { Authorization: "PRIVATE" } }));
    assert.deepEqual(await result.json(), data());
    assert.equal(result.headers.get("Cache-Control"), "no-store");
});
for (const cause of ["origin", "account", "large"]) {
    test("write rejects " + cause, async t => {
        let calls = 0;
        t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json(data()); });
        if (cause === "account") process.env.APP_MODE = "account";
        const response = await modelSettingsProxy(new Request(origin + "/api/model-settings", {
            method: "PUT", headers: { Origin: cause === "origin" ? "https://evil.invalid" : origin, "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: cause === "large" ? "x".repeat(20000) : "PRIVATE" }),
        }));
        assert.equal(calls, 0);
        assert.ok(response.status >= 400);
        assert.ok(!(await response.text()).includes("PRIVATE"));
    });
}
test("upstream error and secret response cannot echo secrets", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ api_key: "PRIVATE" }));
    const response = await modelSettingsProxy(new Request(origin + "/api/model-settings"));
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes("PRIVATE"));
});
test("dimension response is strictly numeric", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ dimensions: 3 }));
    const response = await modelSettingsProxy(new Request(origin + "/api/model-settings/detect-dimensions", {
        method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: "{}",
    }), true);
    assert.deepEqual(await response.json(), { dimensions: 3 });
});
test("custom provider keeps unknown pricing and rejects fabricated cost", () => {
    const event = { type: "RUN_FINISHED", steps_taken: 1, metrics: { model_usage: null, model_duration_ms: 1,
        tool_duration_ms: 0, estimated_cost_cny: null as string | null, pricing: null } };
    assert.ok(parseAgentStreamLine(JSON.stringify(event)));
    event.metrics.estimated_cost_cny = "1";
    assert.throws(() => parseAgentStreamLine(JSON.stringify(event)));
});
