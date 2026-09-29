import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { POST } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/write-grant/assessment/route.ts";

const originalEnv = { ...process.env };
const workspaceId = "a".repeat(32);
const taskId = "b".repeat(32);
const proposalId = "c".repeat(32);
const grantId = "d".repeat(32);
const token = "e".repeat(64);
const origin = "http://localhost:3000";
const backend = "http://127.0.0.1:8000";
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/assessment`;
const context = { params: Promise.resolve({ workspaceId, taskId, proposalId }) };
const INVALID = "invalid_project_write_assessment_input";
const FAILED = "project_write_assessment_read_failed";
const payload = { grant_id: grantId, revision: 1, apply_requested: true };
const results = [
    "invalid_facts", "not_authorized", "apply_not_requested", "grant_missing",
    "grant_revoked", "grant_changed", "target_changed", "proposal_not_approved",
    "application_not_idle", "diff_incomplete", "baseline_changed", "candidate_changed",
    "filesystem_unconfirmed", "platform_unsupported", "exclusive_access_unconfirmed",
];

function receipt(result = "exclusive_access_unconfirmed") {
    return { workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId, result };
}

function req(body: unknown = payload, init: RequestInit = {}): Request {
    return new Request(origin + "/api" + path, {
        method: "POST",
        headers: { Origin: origin, "Content-Type": "application/json" },
        body: JSON.stringify(body),
        ...init,
    });
}

function assertHeaders(response: Response): void {
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(response.headers.get("x-internal"), null);
}

async function assertError(response: Response, status: number, code = FAILED): Promise<void> {
    assert.equal(response.status, status);
    assertHeaders(response);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(typeof body.message, "string");
    assert.ok(body.message.length > 0);
    assert.ok(!JSON.stringify(body).includes("PRIVATE"));
}

beforeEach(() => {
    process.env.APP_MODE = "local";
    process.env.API_BASE_URL = backend;
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...originalEnv }; });

// 走真实路由导出，验证参数解析、固定上游地址与公开投影；不访问真实后端。
for (const result of results) {
    test(`public classification: ${result}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), backend + path);
            assert.equal(init?.method, "POST");
            assert.equal(init?.cache, "no-store");
            assert.equal(init?.redirect, "error");
            assert.ok(init?.signal instanceof AbortSignal);
            assert.deepEqual(JSON.parse(String(init?.body)), payload);
            const headers = new Headers(init?.headers);
            assert.deepEqual([...headers.entries()].sort(), [
                ["content-type", "application/json"],
                ["origin", origin],
                ["x-local-runtime-token", token],
            ]);
            return Response.json({ ...receipt(result), target: "PRIVATE", token: "PRIVATE" }, {
                headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" },
            });
        });
        const request = req();
        for (const header of ["cookie", "authorization", "x-local-runtime-token", "x-user-id"]) {
            request.headers.set(header, "PRIVATE");
        }
        const response = await POST(request, context);
        assert.equal(response.status, 200);
        assertHeaders(response);
        assert.deepEqual(await response.json(), receipt(result));
        assert.equal(mock.mock.callCount(), 1);
    });
}

for (const revision of [1, 2, Number.MAX_SAFE_INTEGER]) {
    test(`explicit false and safe revision ${revision} survive forwarding`, async t => {
        const body = { ...payload, revision, apply_requested: false };
        const mock = t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.deepEqual(JSON.parse(String(init?.body)), body);
            return Response.json(receipt("apply_not_requested"));
        });
        const request = req(body);
        request.headers.set("content-type", "Application/JSON; charset=utf-8");
        const response = await POST(request, context);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), receipt("apply_not_requested"));
        assert.equal(mock.mock.callCount(), 1);
    });
}

const invalidBodies: [string, unknown][] = [
    ["null", null], ["array", []], ["string", "PRIVATE"], ["empty", {}],
    ["missing grant", { revision: 1, apply_requested: true }],
    ["missing revision", { grant_id: grantId, apply_requested: true }],
    ["missing intent", { grant_id: grantId, revision: 1 }],
    ["extra trusted fact", { ...payload, exclusive_access: true }],
    ["wrong grant type", { ...payload, grant_id: 1 }],
    ["short grant", { ...payload, grant_id: "d".repeat(31) }],
    ["uppercase grant", { ...payload, grant_id: "D".repeat(32) }],
    ["newline grant", { ...payload, grant_id: grantId + "\n" }],
    ...["1", true, null, 0, -1, 1.5, 2 ** 53].map(value =>
        [`revision ${String(value)}`, { ...payload, revision: value }] as [string, unknown]),
    ...["false", 0, 1, null].map(value =>
        [`intent ${String(value)}`, { ...payload, apply_requested: value }] as [string, unknown]),
];
for (const [label, body] of invalidBodies) {
    test(`reject input: ${label}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => { throw Error("must not forward"); });
        await assertError(await POST(req(body), context), 422, INVALID);
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const key of ["workspaceId", "taskId", "proposalId"] as const) {
    for (const value of ["x".repeat(32), "a".repeat(31), "a".repeat(32) + "\n"]) {
        test(`reject route parameter ${key}: ${JSON.stringify(value)}`, async t => {
            const mock = t.mock.method(globalThis, "fetch", async () => { throw Error("must not forward"); });
            const params = { workspaceId, taskId, proposalId, [key]: value };
            await assertError(await POST(req(), { params: Promise.resolve(params) }), 422, INVALID);
            assert.equal(mock.mock.callCount(), 0);
        });
    }
}

for (const kind of [
    "account", "invalid-mode", "missing-token", "invalid-token", "remote-backend", "https-backend",
    "remote-host", "missing-origin", "null-origin", "other-origin", "allowed-but-not-same-origin",
    "disallowed-origin", "cross-site", "query", "duplicate-query", "method", "missing-type",
    "wrong-type", "malformed-body", "empty-body",
]) {
    test(`reject boundary: ${kind}`, async t => {
        let request = req();
        let status = 403;
        let code = "local_access_rejected";
        if (kind === "account") { process.env.APP_MODE = "account"; code = "local_mode_required"; }
        if (kind === "invalid-mode") process.env.APP_MODE = "PRIVATE";
        if (kind === "missing-token") delete process.env.LOCAL_RUNTIME_TOKEN;
        if (kind === "invalid-token") process.env.LOCAL_RUNTIME_TOKEN = "PRIVATE";
        if (kind === "remote-backend") process.env.API_BASE_URL = "http://evil.test";
        if (kind === "https-backend") process.env.API_BASE_URL = "https://127.0.0.1:8000";
        if (kind === "remote-host") {
            request = new Request("http://evil.test/api" + path, request);
            request.headers.set("origin", "http://evil.test");
            process.env.AUTH_ALLOWED_ORIGINS = "http://evil.test";
        }
        if (kind === "missing-origin") { request.headers.delete("origin"); code = "workspace_origin_rejected"; }
        if (["null-origin", "other-origin", "allowed-but-not-same-origin"].includes(kind)) {
            request.headers.set("origin", kind === "null-origin" ? "null" : "http://127.0.0.1:3000");
            if (kind === "allowed-but-not-same-origin") process.env.AUTH_ALLOWED_ORIGINS += ",http://127.0.0.1:3000";
            code = "workspace_origin_rejected";
        }
        if (kind === "disallowed-origin") process.env.AUTH_ALLOWED_ORIGINS = "http://localhost:4000";
        if (kind === "cross-site") request.headers.set("sec-fetch-site", "cross-site");
        if (kind === "query" || kind === "duplicate-query") {
            request = new Request(request.url + (kind === "query" ? "?user_id=PRIVATE" : "?revision=1&revision=2"), request);
            status = 422; code = INVALID;
        }
        if (kind === "method") {
            request = new Request(request.url, { method: "GET" });
            status = 405; code = "project_write_assessment_method_not_allowed";
        }
        if (kind === "missing-type" || kind === "wrong-type") {
            if (kind === "missing-type") request.headers.delete("content-type");
            else request.headers.set("content-type", "text/plain");
            status = 415; code = "unsupported_workspace_content_type";
        }
        if (kind === "malformed-body" || kind === "empty-body") {
            request = req(undefined, { body: kind === "malformed-body" ? "PRIVATE" : "" });
            status = 422; code = INVALID;
        }
        const mock = t.mock.method(globalThis, "fetch", async () => { throw Error("must not forward"); });
        await assertError(await POST(request, context), status, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}

const badReceipts: [string, unknown][] = [
    ["null", null], ["array", []], ["missing result", { workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId }],
     ["unknown result", receipt("PRIVATE")],
    ["non-string result", { ...receipt(), result: true }],
];
for (const field of ["workspace_id", "task_id", "proposal_id"] as const) {
    badReceipts.push([`mismatched ${field}`, { ...receipt(), [field]: "f".repeat(32) }]);
    const value: Record<string, unknown> = receipt();
    delete value[field];
    badReceipts.push([`missing ${field}`, value]);
}
for (const [label, body] of badReceipts) {
    test(`untrusted receipt: ${label}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(body));
        await assertError(await POST(req(), context), 502);
        assert.equal(mock.mock.callCount(), 1);
    });
}

const errors: [number, string][] = [
    [400, "invalid_workspace_request"], [401, "invalid_login_session"],
    [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"],
    [404, "workspace_not_accessible"], [415, "unsupported_workspace_content_type"],
    [422, INVALID], [500, FAILED],
];
for (const [status, code] of errors) {
    test(`allow only mapped error ${status}/${code}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({
            code, message: "PRIVATE", detail: "PRIVATE",
        }, { status, headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" } }));
        await assertError(await POST(req(), context), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, code] of [
    [403, "workspace_not_accessible"], [404, "invalid_login_session"],
    [500, "project_write_grant_uncertain"], [500, "PRIVATE"], [422, "invalid_project_write_grant_input"],
] as const) {
    test(`reject wrong status/code ${status}/${code}`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code }, { status }));
        await assertError(await POST(req(), context), 502);
    });
}
for (const status of [201, 202, 204, 302, 409, 429, 503]) {
    test(`unexpected status ${status} cancels upstream body`, async t => {
        const response = new Response(status === 204 ? null : "PRIVATE", { status });
        let cancelled = false;
        if (response.body) t.mock.method(response.body, "cancel", async () => { cancelled = true; });
        const mock = t.mock.method(globalThis, "fetch", async () => response);
        await assertError(await POST(req(), context), 502);
        assert.equal(cancelled, status !== 204);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const kind of ["network", "redirect", "malformed-json", "wrong-type", "missing-type", "body-failure", "error-null", "error-array", "error-no-code"]) {
    test(`upstream failure preserves unknown: ${kind}`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async () => {
            if (kind === "network" || kind === "redirect") throw new TypeError("PRIVATE");
            if (kind === "malformed-json") return new Response("PRIVATE", { headers: { "Content-Type": "application/json" } });
            if (kind === "error-null") return Response.json(null, { status: 500 });
            if (kind === "error-array") return Response.json([], { status: 500 });
            if (kind === "error-no-code") return Response.json({ message: "PRIVATE" }, { status: 500 });
            const response = Response.json(receipt());
            if (kind === "wrong-type") response.headers.set("content-type", "text/html");
            if (kind === "missing-type") response.headers.delete("content-type");
            if (kind === "body-failure") t.mock.method(response, "json", async () => { throw Error("PRIVATE"); });
            return response;
        });
        await assertError(await POST(req(), context), 502);
        assert.equal(mock.mock.callCount(), 1);
    });
}

// 取消覆盖请求正文、fetch和响应正文；错误与成功正文均不能覆盖取消事实。
for (const phase of ["before", "request-body", "request-body-error", "fetch", "response-body", "error-body", "timeout-fetch", "timeout-body"]) {
    test(`cancellation: ${phase}`, async t => {
        const controller = new AbortController();
        const timedOut = phase.startsWith("timeout");
        if (timedOut) t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
            assert.equal(milliseconds, 20_000);
            return controller.signal;
        });
        const request = req(undefined, timedOut ? {} : { signal: controller.signal });
        if (phase === "before") controller.abort();
        if (phase.startsWith("request-body")) t.mock.method(request, "json", async () => {
            controller.abort();
            if (phase === "request-body-error") throw Error("PRIVATE");
            return payload;
        });
        const mock = t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.ok(init?.signal);
            if (phase === "fetch" || phase === "timeout-fetch") {
                // 在真实传入的组合信号上观察中止，而非只伪造fetch抛错。
                const pending = new Promise<Response>((_resolve, reject) => {
                    init.signal!.addEventListener("abort", () => reject(init.signal!.reason), { once: true });
                });
                controller.abort();
                assert.equal(init.signal.aborted, true);
                return pending;
            }
            const response = phase === "error-body"
                ? Response.json({ code: "workspace_not_accessible" }, { status: 404 })
                : Response.json(receipt());
            const raw = phase === "error-body" ? { code: "workspace_not_accessible" } : receipt();
            t.mock.method(response, "json", async () => {
                controller.abort();
                assert.equal(init.signal!.aborted, true);
                return raw;
            });
            return response;
        });
        await assertError(await POST(request, context), timedOut ? 504 : 499);
        assert.equal(mock.mock.callCount(), phase === "before" || phase.startsWith("request-body") ? 0 : 1);
    });
}
