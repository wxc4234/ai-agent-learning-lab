import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { GET, POST } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/write-grant/route.ts";
import { POST as REVOKE } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/write-grant/revoke/route.ts";

const env = { ...process.env };
const workspaceId = "a".repeat(32), taskId = "b".repeat(32), proposalId = "c".repeat(32), grantId = "d".repeat(32);
const origin = "http://localhost:3000", backend = "http://127.0.0.1:8000", token = "e".repeat(64);
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant`;
const context = { params: Promise.resolve({ workspaceId, taskId, proposalId }) };
const operations = ["read", "issue", "revoke"] as const;
type Op = typeof operations[number];
const handlers = { read: GET, issue: POST, revoke: REVOKE };
const payload = (op: Op) => op === "issue" ? { action: "grant" } : { grant_id: grantId, revision: 1 };
const receipt = (op: Op) => ({ workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId,
    grant: { grant_id: grantId, revision: op === "revoke" ? 2 : 1, status: op === "revoke" ? "revoked" : "enabled" } });
function req(op: Op, body: unknown = payload(op), init: RequestInit = {}) {
    return new Request(origin + "/api" + path + (op === "revoke" ? "/revoke" : ""), {
        method: op === "read" ? "GET" : "POST", headers: { Origin: origin, "Content-Type": "application/json" },
        ...(op === "read" ? {} : { body: JSON.stringify(body) }), ...init,
    });
}
async function error(res: Response, status: number, code: string) {
    assert.equal(res.status, status); assert.equal(res.headers.get("cache-control"), "no-store");
    assert.equal(res.headers.get("set-cookie"), null);
    const data = await res.json();
    assert.deepEqual(Object.keys(data).sort(), ["code", "message"]);
    assert.equal(data.code, code); assert.ok(!JSON.stringify(data).includes("PRIVATE"));
}
const failed = (op: Op) => op === "read" ? "project_write_grant_read_failed" : "project_write_grant_uncertain";
beforeEach(() => {
    process.env.APP_MODE = "local"; process.env.API_BASE_URL = backend;
    process.env.LOCAL_RUNTIME_TOKEN = token; process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...env }; });

for (const op of operations) {
    test(`${op}: exact forwarding and public projection`, async t => {
        const mock = t.mock.method(globalThis, "fetch", async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), backend + path + (op === "revoke" ? "/revoke" : ""));
            assert.equal(init?.method, op === "read" ? "GET" : "POST");
            assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error"); assert.ok(init?.signal);
            assert.equal(init?.body, op === "read" ? undefined : JSON.stringify(payload(op)));
            const headers = new Headers(init?.headers);
            assert.equal(headers.get("x-local-runtime-token"), token);
            assert.equal(headers.get("cookie"), null); assert.equal(headers.get("authorization"), null);
            const data = receipt(op);
            return Response.json({ ...data, target: "PRIVATE", grant: { ...data.grant, target: "PRIVATE" } }, {
                status: op === "issue" ? 201 : 200, headers: { "Set-Cookie": "PRIVATE", "X-Internal": "PRIVATE" },
            });
        });
        const request = req(op);
        request.headers.set("cookie", "PRIVATE"); request.headers.set("authorization", "PRIVATE"); request.headers.set("x-local-runtime-token", "PRIVATE");
        const response = await handlers[op](request, context);
        assert.equal(response.status, op === "issue" ? 201 : 200); assert.deepEqual(await response.json(), receipt(op));
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("set-cookie"), null); assert.equal(response.headers.get("x-internal"), null);
        assert.equal(mock.mock.callCount(), 1);
    });
    for (const kind of ["account", "token", "backend", "host", "origin", "cross-site", "query", "path", "method"]) {
        test(`${op}: reject ${kind} before fetch`, async t => {
            let request = req(op), ctx = context;
            let status = 403, code = "local_access_rejected";
            if (kind === "account") { process.env.APP_MODE = "account"; code = "local_mode_required"; }
            if (kind === "token") delete process.env.LOCAL_RUNTIME_TOKEN;
            if (kind === "backend") process.env.API_BASE_URL = "http://evil.test";
            if (kind === "host") request = new Request("http://evil.test/api" + path, request);
            if (kind === "origin") request.headers.set("origin", "https://evil.test");
            if (kind === "cross-site") request.headers.set("sec-fetch-site", "cross-site");
            if (kind === "query") { request = new Request(request.url + "?user_id=PRIVATE", request); status = 422; code = "invalid_project_write_grant_input"; }
            if (kind === "path") { ctx = { params: Promise.resolve({ workspaceId: "PRIVATE", taskId, proposalId }) }; status = 422; code = "invalid_project_write_grant_input"; }
            if (kind === "method") { request = new Request(request.url, { method: "DELETE" }); status = 405; code = "project_write_grant_method_not_allowed"; }
            const mock = t.mock.method(globalThis, "fetch", () => { throw Error("must not fetch"); });
            await error(await handlers[op](request, ctx), status, code); assert.equal(mock.mock.callCount(), 0);
        });
    }
    for (const kind of ["resource", "revision", "id", "state", "missing", "array", "null", "malformed", "status", "network", "unknown-code", "wrong-code-status"]) {
        test(`${op}: ${kind} cannot be success`, async t => {
            const mock = t.mock.method(globalThis, "fetch", async () => {
                if (kind === "network") throw Error("PRIVATE");
                if (kind === "malformed") return new Response("PRIVATE", { status: op === "issue" ? 201 : 200 });
                if (kind === "unknown-code") return Response.json({ code: "PRIVATE" }, { status: 500 });
                if (kind === "wrong-code-status") return Response.json({ code: "workspace_not_accessible" }, { status: 409 });
                const data: Record<string, unknown> = receipt(op);
                if (kind === "resource") data.task_id = "f".repeat(32);
                if (kind === "revision") data.grant = { ...receipt(op).grant, revision: true };
                if (kind === "id") data.grant = { ...receipt(op).grant, grant_id: "PRIVATE" };
                if (kind === "state") data.grant = { ...receipt(op).grant, status: "unknown" };
                if (kind === "missing") delete data.grant;
                return Response.json(kind === "array" ? [] : kind === "null" ? null : data, { status: kind === "status" ? 202 : op === "issue" ? 201 : 200 });
            });
            await error(await handlers[op](req(op), context), 502, failed(op)); assert.equal(mock.mock.callCount(), 1);
        });
    }
    for (const phase of ["before", "fetch", "body", "timeout"]) {
        test(`${op}: cancellation ${phase}`, async t => {
            const controller = new AbortController();
            if (phase === "before") controller.abort();
            if (phase === "timeout") t.mock.method(AbortSignal, "timeout", () => controller.signal);
            const mock = t.mock.method(globalThis, "fetch", async () => {
                if (phase === "fetch" || phase === "timeout") { controller.abort(); throw Error("PRIVATE"); }
                const response = Response.json(receipt(op), { status: op === "issue" ? 201 : 200 });
                t.mock.method(response, "json", async () => { controller.abort(); return receipt(op); }); return response;
            });
            await error(await handlers[op](req(op, undefined, phase === "timeout" ? {} : { signal: controller.signal }), context),
                phase === "timeout" ? 504 : 499, phase === "before" && op !== "read" ? "project_write_grant_not_forwarded" : failed(op));
            assert.equal(mock.mock.callCount(), phase === "before" ? 0 : 1);
        });
    }
    for (const [status, code] of [[404, "workspace_not_accessible"], [422, "invalid_project_write_grant_input"], [500, failed(op)]] as const) {
        test(`${op}: safe mapped error ${status}`, async t => {
            t.mock.method(globalThis, "fetch", async () => Response.json({ code, message: "PRIVATE" }, { status }));
            await error(await handlers[op](req(op), context), status, code);
        });
    }
}
for (const op of ["issue", "revoke"] as const) {
    const invalid = op === "issue" ? [null, [], {}, { action: true }, { action: "grant", target: "PRIVATE" }]
        : [null, {}, { grant_id: grantId, revision: true }, { grant_id: grantId, revision: "1" }, { grant_id: grantId, revision: 0 }, { grant_id: grantId, revision: 2**53 }, { grant_id: grantId, revision: 1, target: "PRIVATE" }];
    for (const [index, body] of invalid.entries()) {
        test(`${op}: invalid body ${index}`, async t => {
            const mock = t.mock.method(globalThis, "fetch");
            await error(await handlers[op](req(op, body), context), 422, "invalid_project_write_grant_input"); assert.equal(mock.mock.callCount(), 0);
        });
    }
    test(`${op}: origin, content type and parse failure`, async t => {
        const mock = t.mock.method(globalThis, "fetch");
        const noOrigin = req(op); noOrigin.headers.delete("origin");
        await error(await handlers[op](noOrigin, context), 403, "workspace_origin_rejected");
        const wrongType = req(op); wrongType.headers.set("content-type", "text/plain");
        await error(await handlers[op](wrongType, context), 415, "unsupported_workspace_content_type");
        await error(await handlers[op](req(op, undefined, { body: "PRIVATE" }), context), 422, "invalid_project_write_grant_input");
        assert.equal(mock.mock.callCount(), 0);
    });
    test(`${op}: null cannot mean write success`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ ...receipt(op), grant: null }, { status: op === "issue" ? 201 : 200 }));
        await error(await handlers[op](req(op), context), 502, failed(op));
    });
    test(`${op}: known conflict`, async t => {
        t.mock.method(globalThis, "fetch", async () => Response.json({ code: "project_write_grant_conflict", message: "PRIVATE" }, { status: 409 }));
        await error(await handlers[op](req(op), context), 409, "project_write_grant_conflict");
    });
}
test("read: absent origin and explicit null mean authorized missing record", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ ...receipt("read"), grant: null }));
    const request = req("read"); request.headers.delete("origin");
    const response = await GET(request, context); assert.equal(response.status, 200); assert.equal((await response.json()).grant, null);
});
test("revoke: mismatched receipt cannot confirm request", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json(receipt("revoke")));
    for (const body of [{ grant_id: "f".repeat(32), revision: 1 }, { grant_id: grantId, revision: 2 }]) {
        await error(await REVOKE(req("revoke", body), context), 502, failed("revoke"));
    }
});

test("read: revoked is a record state, not missing", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json(receipt("revoke")));
    const response = await GET(req("read"), context);
    assert.equal(response.status, 200); assert.deepEqual(await response.json(), receipt("revoke"));
});
test("read: body is rejected before forwarding", async t => {
    const request = req("read");
    // Fetch Request正常禁止GET正文；此替身验证代理对适配层异常输入仍拒绝。
    Object.defineProperty(request, "body", { value: new ReadableStream() });
    const mock = t.mock.method(globalThis, "fetch");
    await error(await GET(request, context), 422, "invalid_project_write_grant_input");
    assert.equal(mock.mock.callCount(), 0);
});
test("issue: revoked receipt cannot confirm new grant", async t => {
    t.mock.method(globalThis, "fetch", async () => Response.json(receipt("revoke"), { status: 201 }));
    await error(await POST(req("issue"), context), 502, failed("issue"));
});
for (const op of ["issue", "revoke"] as const) {
    test(`${op}: cancellation while reading browser body never forwards`, async t => {
        const controller = new AbortController();
        const request = req(op, undefined, { signal: controller.signal });
        t.mock.method(request, "json", async () => { controller.abort(); return payload(op); });
        const mock = t.mock.method(globalThis, "fetch");
        await error(await handlers[op](request, context), 499, "project_write_grant_not_forwarded");
        assert.equal(mock.mock.callCount(), 0);
    });
}
test("read: unexpected upstream status cancels unused stream", async t => {
    let cancelled = false;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
        cancel() { cancelled = true; },
    }), { status: 302 }));
    await error(await GET(req("read"), context), 502, failed("read"));
    assert.ok(cancelled);
});
