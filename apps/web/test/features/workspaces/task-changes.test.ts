import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { readTaskChanges, changeStatus } from '../../../src/features/workbench/task-changes-data.ts';
import { GET } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/route.ts';
const workspaceId = 'a'.repeat(32), taskId = 'b'.repeat(32);
const env = { ...process.env };
const item = { proposal_id: 'c'.repeat(32), relative_path: 'src/中文.ts', status: 'pending' as const, application_status: 'idle' as const, diff_truncated: false };
const page = () => ({ workspace_id: workspaceId, task_id: taskId, items: [item], next_cursor: null });
const call = (query = '', init?: RequestInit) => GET(new Request(`http://localhost:3000/api/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals${query}`, init), { params: Promise.resolve({ workspaceId, taskId }) });
beforeEach(() => { process.env.APP_MODE = 'local'; process.env.LOCAL_RUNTIME_TOKEN = 'd'.repeat(64); process.env.API_BASE_URL = 'http://127.0.0.1:8000'; });
afterEach(() => { process.env = { ...env }; });
test('projection excludes internal fields and preserves identity', () => {
    assert.deepEqual(readTaskChanges({ ...page(), internal: 'PRIVATE', items: [{ ...item, bound_root: 'PRIVATE' }] }, workspaceId, taskId), page());
    assert.equal(readTaskChanges(page(), taskId, workspaceId), null);
});
for (const patch of [{ status: 'new' }, { application_status: 'unknown' }, { application_status: 'applied' }, { status: 'approved', diff_truncated: true }, { proposal_id: 'invalid' }]) {
    test(`reject invalid item ${JSON.stringify(patch)}`, () => assert.equal(readTaskChanges({ ...page(), items: [{ ...item, ...patch }] }, workspaceId, taskId), null));
}
for (const [status, application, label] of [
    ['pending', 'idle', '待审批'], ['approved', 'idle', '已批准 · 未应用'], ['rejected', 'idle', '已拒绝'],
    ['approved', 'applied', '已应用'], ['approved', 'running', '应用中'], ['approved', 'uncertain', '应用结果未确认'], ['approved', 'not_applied', '未应用'],
] as const) test(`status ${status}/${application}`, () => assert.equal(changeStatus({ ...item, status, application_status: application }), label));
test('BFF uses internal credentials, no-store, explicit projection', async t => {
    t.mock.method(globalThis, 'fetch', async (_url: unknown, init: RequestInit) => {
        assert.deepEqual(init.headers, { 'X-Local-Runtime-Token': 'd'.repeat(64) });
        assert.equal(init.cache, 'no-store'); assert.equal(init.redirect, 'error');
        return Response.json({ ...page(), private: 'PRIVATE' }, { headers: { 'Set-Cookie': 'PRIVATE' } });
    });
    const result = await call('', { headers: { cookie: 'PRIVATE', 'X-Local-Runtime-Token': 'PRIVATE' } });
    assert.equal(result.status, 200); assert.equal(result.headers.get('set-cookie'), null);
    assert.deepEqual(await result.json(), page());
});
for (const query of ['?before=1%0A', '?before=0', '?before=-1', '?user_id=1', '?before=1&before=2', '?before=2147483648']) test(`reject ${query}`, async t => {
    const fetch = t.mock.method(globalThis, 'fetch'); assert.equal((await call(query)).status, 422); assert.equal(fetch.mock.callCount(), 0);
});
for (const status of [404, 500]) test(`upstream ${status}`, async t => {
    t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE', { status }));
    const result = await call(); assert.equal(result.status, status === 404 ? 404 : 502); assert.ok(!(await result.text()).includes('PRIVATE'));
});
test('mismatched task response fails closed', async t => {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ ...page(), task_id: workspaceId }));
    assert.equal((await call()).status, 502);
});
test('cancellation prevents request', async t => {
    const controller = new AbortController(); controller.abort();
    const fetch = t.mock.method(globalThis, 'fetch');
    assert.equal((await call('', { signal: controller.signal })).status, 502); assert.equal(fetch.mock.callCount(), 0);
});

test('cursor with trailing newline is rejected', () => {
    assert.equal(readTaskChanges({ ...page(), next_cursor: '1\n' }, workspaceId, taskId), null);
});
test('network failure is redacted', async t => {
    t.mock.method(globalThis, 'fetch', async () => { throw new Error('PRIVATE'); });
    const result = await call();
    assert.equal(result.status, 502);
    assert.ok(!(await result.text()).includes('PRIVATE'));
});
