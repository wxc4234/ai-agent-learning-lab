import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { POST, runtime } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/apply/route.ts";

const originalEnv = { ...process.env };
const workspaceId = "a".repeat(32);
const taskId = "b".repeat(32);
const proposalId = "c".repeat(32);
const token = "d".repeat(64);
const origin = "http://localhost:3000";
const backend = "http://127.0.0.1:8000";
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/apply`;
const receipt = () => ({
    workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId,
    file_status: "replaced", application_status: "applied",
    code: "proposal_application_applied", cleanup_complete: true,
});
function request(body: unknown = { action: "apply" }, init: RequestInit = {}) {
    return new Request(`${origin}/api${path}`, { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify(body), ...init });
}
function route(req = request(), workspace = workspaceId, task = taskId, proposal = proposalId) {
    return POST(req, { params: Promise.resolve({ workspaceId: workspace, taskId: task, proposalId: proposal }) });
}
async function error(response: Response, status: number, code: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("set-cookie"), null);
    const body = await response.json();
    assert.deepEqual(Object.keys(body).sort(), ["code", "message"]);
    assert.equal(body.code, code);
    assert.equal(typeof body.message, "string");
    assert.ok(!JSON.stringify(body).includes("PRIVATE"));
    if (code === "proposal_execution_uncertain") assert.equal(body.message, "本次执行结果未确认，不能据此判断文件未修改，请勿重复提交");
}
beforeEach(() => {
    process.env.APP_MODE = "local";
    process.env.API_BASE_URL = backend;
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...originalEnv }; });

for (const action of ["apply"]) {
    test(`trusted ${action} forwarding and public response`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), backend + path);
            assert.equal(init?.method, "POST");
            assert.equal(init?.cache, "no-store");
            assert.equal(init?.redirect, "error");
            assert.ok(init?.signal);
            assert.equal(init?.body, JSON.stringify({ action }));
            assert.deepEqual([...new Headers(init?.headers)], [["content-type", "application/json"], ["origin", origin], ["x-local-runtime-token", token]]);
            return Response.json({ ...receipt(), proposed_content: "PRIVATE", bound_root: "PRIVATE" }, { headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" } });
        });
        const response = await route(request({ action }, { headers: { Origin: origin, "Content-Type": "Application/JSON; charset=utf-8", Cookie: "PRIVATE", Authorization: "PRIVATE", "X-Local-Runtime-Token": "forged" } }));
        assert.equal(runtime, "nodejs");
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("set-cookie"), null);
        assert.equal(response.headers.get("x-internal"), null);
        assert.deepEqual(await response.json(), receipt());
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const kind of ["account", "invalid-mode", "missing-token", "invalid-token", "remote-backend", "https-backend", "remote-host", "missing-origin", "foreign-origin", "cross-site"]) {
    test(`boundary ${kind} rejects before fetch`, async (t) => {
        const req = request();
        let actual = req;
        if (kind === "account") process.env.APP_MODE = "account";
        if (kind === "invalid-mode") process.env.APP_MODE = "PRIVATE";
        if (kind === "missing-token") delete process.env.LOCAL_RUNTIME_TOKEN;
        if (kind === "invalid-token") process.env.LOCAL_RUNTIME_TOKEN = "PRIVATE";
        if (kind === "remote-backend") process.env.API_BASE_URL = "http://evil.test";
        if (kind === "https-backend") process.env.API_BASE_URL = "https://localhost:8000";
        if (kind === "remote-host") actual = new Request("http://evil.test/api" + path, req);
        if (kind === "missing-origin") req.headers.delete("origin");
        if (kind === "foreign-origin") req.headers.set("origin", "http://localhost:3000.evil.test");
        if (kind === "cross-site") req.headers.set("sec-fetch-site", "cross-site");
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(actual), 403, kind === "account" ? "local_mode_required" : kind === "missing-origin" ? "workspace_origin_rejected" : "local_access_rejected");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const field of ["workspace", "task", "proposal"]) {
    for (const invalid of ["bad", "A".repeat(32), "a".repeat(32) + "\n"]) {
        test(`invalid ${field} ${JSON.stringify(invalid)}`, async (t) => {
            const mock = t.mock.method(globalThis, "fetch");
            await error(await route(request(), field === "workspace" ? invalid : workspaceId, field === "task" ? invalid : taskId, field === "proposal" ? invalid : proposalId), 422, "invalid_proposal_execution_input");
            assert.equal(mock.mock.callCount(), 0);
        });
    }
}
for (const query of ["user_id=1", "action=retry", "x=1&x=2"]) {
    test(`query ${query} rejected`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(new Request(`${origin}/api${path}?${query}`, request())), 422, "invalid_proposal_execution_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [index, body] of [null, [], true, "PRIVATE", {}, { action: null }, { action: true }, { action: 1 }, { action: "retry" }, { action: "APPLY" }, { action: "apply", user_id: 1 }, { action: "apply", proposed_content: "PRIVATE" }, { action: "apply", path: "/PRIVATE" }, { action: "apply", skip_checks: true }].entries()) {
    test(`strict body ${index}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(request(body)), 422, "invalid_proposal_execution_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const kind of ["missing-type", "wrong-type", "malformed", "empty"]) {
    test(`body ${kind}`, async (t) => {
        const req = request(undefined, kind === "malformed" ? { body: "PRIVATE{" } : kind === "empty" ? { body: "" } : {});
        if (kind === "missing-type") req.headers.delete("content-type");
        if (kind === "wrong-type") req.headers.set("content-type", "text/plain");
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(req), kind.endsWith("type") ? 415 : 422, kind.endsWith("type") ? "unsupported_workspace_content_type" : "invalid_proposal_execution_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [status, code] of [
    [400, "invalid_workspace_request"], [401, "invalid_login_session"], [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"], [404, "workspace_not_accessible"], [409, "sample_execution_unavailable"], [415, "unsupported_workspace_content_type"], [422, "invalid_proposal_execution_input"], [500, "proposal_execution_uncertain"],
] as const) {
    test(`fixed error ${status}/${code}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE", user_id: 1 }, { status, headers: { "Set-Cookie": "PRIVATE" } }));
        await error(await route(), status, code);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [status, payload] of [[409, { code: "workspace_not_accessible" }], [404, { code: "proposal_state_conflict" }], [500, { code: "PRIVATE" }], [409, { code: "toString" }], [409, { code: "__proto__" }], [422, null], [403, []], [400, { code: 1 }]] as const) {
    test(`unknown error ${status}/${JSON.stringify(payload)}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json(payload, { status }));
        await error(await route(), 502, "proposal_execution_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [field, value] of [["workspace_id", proposalId], ["task_id", workspaceId], ["proposal_id", taskId], ["file_status", "not_attempted"], ["application_status", "running"], ["code", "PRIVATE"], ["cleanup_complete", null]] as const) {
    test(`mismatched receipt ${field}/${value}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ ...receipt(), [field]: value }));
        await error(await route(), 502, "proposal_execution_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const raw of [null, [], true, "PRIVATE", ...Object.keys(receipt()).map(key => ({ ...receipt(), [key]: undefined }))]) {
    test(`invalid receipt ${JSON.stringify(raw)}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json(raw));
        await error(await route(), 502, "proposal_execution_uncertain");
    });
}
// HTTP 200只证明取得合法回执，不把未知登记或不确定文件结果当作成功。
for (const outcome of [
    { file_status: "replaced", application_status: "unknown", code: "proposal_application_registration_unconfirmed", cleanup_complete: true },
    { file_status: "not_attempted", application_status: "unknown", code: "proposal_application_claim_unconfirmed", cleanup_complete: null },
    { file_status: "uncertain", application_status: "uncertain", code: "proposal_application_uncertain", cleanup_complete: null },
    { file_status: "not_replaced", application_status: "not_applied", code: "proposal_application_not_applied", cleanup_complete: true },
]) {
    test(`preserves execution evidence ${outcome.code}`, async (t) => {
        const expected = { ...receipt(), ...outcome };
        const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ ...expected, application_token: "PRIVATE" }));
        const response = await route();
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.deepEqual(await response.json(), expected);
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const status of [201, 204, 302, 429, 503]) {
    test(`unexpected ${status} releases unread body`, async (t) => {
        let cancelled = false;
        const response = new Response(status === 204 ? null : new ReadableStream({ cancel() { cancelled = true; } }), { status });
        const json = t.mock.method(response, "json", async () => { throw new Error("must not read"); });
        t.mock.method(globalThis, "fetch", async () => response);
        await error(await route(), 502, "proposal_execution_uncertain");
        assert.equal(json.mock.callCount(), 0);
        assert.equal(cancelled, status !== 204);
    });
}
for (const status of [200, 409, 500]) {
    test(`malformed upstream JSON ${status}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => new Response("PRIVATE{", { status }));
        await error(await route(), 502, "proposal_execution_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
test("network error never retries", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error("PRIVATE"); });
    await error(await route(), 502, "proposal_execution_uncertain");
    assert.equal(mock.mock.callCount(), 1);
});
// 取消由受控事件驱动，不固定sleep，也不等待真实20秒。
for (const phase of ["already", "input-success", "input-error", "before-fetch"]) {
    test(`cancel ${phase} never forwards`, async (t) => {
        const controller = new AbortController();
        const req = request(undefined, { signal: controller.signal });
        if (phase === "already") controller.abort();
        if (phase.startsWith("input")) t.mock.method(req, "json", async () => {
            controller.abort();
            if (phase === "input-error") throw new Error("PRIVATE");
            return { action: "apply" };
        });
        if (phase === "before-fetch") t.mock.method(AbortSignal, "timeout", () => {
            controller.abort();
            return new AbortController().signal;
        });
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(req), 499, "proposal_request_cancelled");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const cause of ["cancel", "timeout"]) {
    for (const phase of ["fetch", "headers", "body", "after-json", "error-body", "unused-body"]) {
        test(`${cause} during ${phase} is uncertain`, async (t) => {
            const controller = new AbortController();
            if (cause === "timeout") t.mock.method(AbortSignal, "timeout", (ms: number) => {
                assert.equal(ms, 20_000);
                return controller.signal;
            });
            const mock = t.mock.method(globalThis, "fetch", async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
                const signal = init?.signal;
                assert.ok(signal);
                if (phase === "fetch") return await new Promise<Response>((_resolve, reject) => {
                    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                    controller.abort();
                });
                if (phase === "headers") { controller.abort(); return Response.json(receipt()); }
                if (phase === "unused-body") return new Response(new ReadableStream({ cancel() { controller.abort(); } }), { status: 503 });
                if (phase === "body" || phase === "error-body") return new Response(new ReadableStream({
                    pull(stream) {
                        signal.addEventListener("abort", () => stream.error(signal.reason), { once: true });
                        controller.abort();
                    },
                }, { highWaterMark: 0 }), { status: phase === "error-body" ? 409 : 200 });
                const response = Response.json(receipt());
                t.mock.method(response, "json", async () => { controller.abort(); return receipt(); });
                return response;
            });
            await error(await route(request(undefined, cause === "cancel" ? { signal: controller.signal } : {})), cause === "cancel" ? 499 : 504, "proposal_execution_uncertain");
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}
