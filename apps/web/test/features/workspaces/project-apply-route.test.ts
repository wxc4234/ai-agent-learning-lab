import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { POST } from "../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/write-grant/apply/route.ts";
const originalEnv = { ...process.env };
afterEach(() => { process.env = { ...originalEnv }; });
const workspaceId = "a".repeat(32), taskId = "b".repeat(32), proposalId = "c".repeat(32);
const grantId = "e".repeat(32), origin = "http://localhost:3000";
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}/write-grant/apply`;
for (const valid of [true, false]) {
    test(`project apply ${valid ? "forwards once" : "rejects target injection"}`, async t => {
        process.env.APP_MODE = "local"; process.env.API_BASE_URL = "http://127.0.0.1:8000";
        process.env.LOCAL_RUNTIME_TOKEN = "d".repeat(64); process.env.AUTH_ALLOWED_ORIGINS = origin;
        const mock = t.mock.method(globalThis, "fetch", async (url: unknown, init: RequestInit) => {
            assert.equal(url, "http://127.0.0.1:8000" + path);
            assert.deepEqual(JSON.parse(String(init.body)), { grant_id: grantId, revision: 1 });
            return Response.json({ workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId,
                file_status: "replaced", application_status: "applied", code: "proposal_application_applied", cleanup_complete: true });
        });
        const response = await POST(new Request(origin + "/api" + path, {
            method: "POST", headers: { Origin: origin, "Content-Type": "application/json" },
            body: JSON.stringify({ grant_id: grantId, revision: 1, ...(valid ? {} : { path: "/etc/passwd" }) }),
        }), { params: Promise.resolve({ workspaceId, taskId, proposalId }) });
        assert.equal(response.status, valid ? 200 : 422);
        assert.equal(mock.mock.callCount(), valid ? 1 : 0);
    });
}
