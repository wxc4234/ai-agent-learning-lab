import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { POST, runtime } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/decision/route.ts";
import { readFileEditProposalDecisionReceipt } from "../../../src/features/workbench/file-edit-proposal-decision-data.ts";

const originalEnv = { ...process.env };
const workspaceId = "a".repeat(32);
const taskId = "b".repeat(32);
const proposalId = "c".repeat(32);
const token = "d".repeat(64);
const origin = "http://localhost:3000";
const backend = "http://127.0.0.1:8000";
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/decision`;
const receipt = (status = "approved") => ({ workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId, status });
function request(body: unknown = { decision: "approved" }, init: RequestInit = {}) {
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
    if (code === "proposal_decision_uncertain") assert.equal(body.message, "提案决策结果未确认，请先查询详情，勿直接重复提交");
}
beforeEach(() => {
    process.env.APP_MODE = "local";
    process.env.API_BASE_URL = backend;
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...originalEnv }; });

for (const decision of ["approved", "rejected"]) {
    test(`trusted ${decision} forwarding and public response`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), backend + path);
            assert.equal(init?.method, "POST");
            assert.equal(init?.cache, "no-store");
            assert.equal(init?.redirect, "error");
            assert.ok(init?.signal);
            assert.equal(init?.body, JSON.stringify({ decision }));
            assert.deepEqual([...new Headers(init?.headers)], [["content-type", "application/json"], ["origin", origin], ["x-local-runtime-token", token]]);
            return Response.json({ ...receipt(decision), proposed_content: "PRIVATE", bound_root: "PRIVATE" }, { headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" } });
        });
        const response = await route(request({ decision }, { headers: { Origin: origin, "Content-Type": "Application/JSON; charset=utf-8", Cookie: "PRIVATE", Authorization: "PRIVATE", "X-Local-Runtime-Token": "forged" } }));
        assert.equal(runtime, "nodejs");
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("set-cookie"), null);
        assert.equal(response.headers.get("x-internal"), null);
        assert.deepEqual(await response.json(), receipt(decision));
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
            await error(await route(request(), field === "workspace" ? invalid : workspaceId, field === "task" ? invalid : taskId, field === "proposal" ? invalid : proposalId), 422, "invalid_proposal_decision_input");
            assert.equal(mock.mock.callCount(), 0);
        });
    }
}
for (const query of ["user_id=1", "decision=rejected", "x=1&x=2"]) {
    test(`query ${query} rejected`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(new Request(`${origin}/api${path}?${query}`, request())), 422, "invalid_proposal_decision_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [index, body] of [null, [], true, "PRIVATE", {}, { decision: null }, { decision: true }, { decision: 1 }, { decision: "pending" }, { decision: "APPROVED" }, { decision: "approved", user_id: 1 }, { decision: "approved", proposed_content: "PRIVATE" }].entries()) {
    test(`strict body ${index}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(request(body)), 422, "invalid_proposal_decision_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const kind of ["missing-type", "wrong-type", "malformed", "empty"]) {
    test(`body ${kind}`, async (t) => {
        const req = request(undefined, kind === "malformed" ? { body: "PRIVATE{" } : kind === "empty" ? { body: "" } : {});
        if (kind === "missing-type") req.headers.delete("content-type");
        if (kind === "wrong-type") req.headers.set("content-type", "text/plain");
        const mock = t.mock.method(globalThis, "fetch");
        await error(await route(req), kind.endsWith("type") ? 415 : 422, kind.endsWith("type") ? "unsupported_workspace_content_type" : "invalid_proposal_decision_input");
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [status, code] of [
    [400, "invalid_workspace_request"], [401, "invalid_login_session"], [403, "local_mode_required"], [403, "local_access_rejected"], [403, "workspace_origin_rejected"], [404, "workspace_not_accessible"], [409, "proposal_binding_changed"], [409, "proposal_state_conflict"], [409, "proposal_diff_incomplete"], [415, "unsupported_workspace_content_type"], [422, "invalid_proposal_decision_input"], [422, "proposal_decision_invalid"], [500, "proposal_decision_uncertain"],
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
        await error(await route(), 502, "proposal_decision_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
for (const [field, value] of [["workspace_id", proposalId], ["task_id", workspaceId], ["proposal_id", taskId], ["status", "rejected"], ["status", "pending"], ["status", null]] as const) {
    test(`mismatched receipt ${field}/${value}`, async (t) => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ ...receipt(), [field]: value }));
        await error(await route(), 502, "proposal_decision_uncertain");
    });
}
test("receipt parser rejects missing fields and nonobjects", () => {
    for (const raw of [null, [], true, "PRIVATE", ...Object.keys(receipt()).map(key => ({ ...receipt(), [key]: undefined }))]) {
        assert.equal(readFileEditProposalDecisionReceipt(raw, workspaceId, taskId, proposalId, "approved"), null);
    }
    assert.equal(readFileEditProposalDecisionReceipt(receipt(), "bad", taskId, proposalId, "approved"), null);
});
for (const status of [201, 204, 302, 429, 503]) {
    test(`unexpected ${status} releases unread body`, async (t) => {
        let cancelled = false;
        const response = new Response(status === 204 ? null : new ReadableStream({ cancel() { cancelled = true; } }), { status });
        const json = t.mock.method(response, "json", async () => { throw new Error("must not read"); });
        t.mock.method(globalThis, "fetch", async () => response);
        await error(await route(), 502, "proposal_decision_uncertain");
        assert.equal(json.mock.callCount(), 0);
        assert.equal(cancelled, status !== 204);
    });
}
for (const status of [200, 409, 500]) {
    test(`malformed upstream JSON ${status}`, async (t) => {
        const mock = t.mock.method(globalThis, "fetch", async () => new Response("PRIVATE{", { status }));
        await error(await route(), 502, "proposal_decision_uncertain");
        assert.equal(mock.mock.callCount(), 1);
    });
}
test("network error never retries", async (t) => {
    const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error("PRIVATE"); });
    await error(await route(), 502, "proposal_decision_uncertain");
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
            return { decision: "approved" };
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
            await error(await route(request(undefined, cause === "cancel" ? { signal: controller.signal } : {})), cause === "cancel" ? 499 : 504, "proposal_decision_uncertain");
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}
