import assert from 'node:assert/strict';
import { test, beforeEach, afterEach } from 'node:test';
import { taskProxy } from '../../../src/app/api/_shared/task-proxy.ts';
const originalFetch = globalThis.fetch;
const originalEnv = { ...process.env };
const workspace = 'a'.repeat(32), task = 'b'.repeat(32);
const item = { external_id: task, workspace_id: workspace, conversation_id: 'c'.repeat(32), title: '任务', created_at: '2026-09-15T00:00:00Z' };
beforeEach(() => {
    process.env.APP_MODE = 'local'; process.env.LOCAL_RUNTIME_TOKEN = 'd'.repeat(64);
    process.env.API_BASE_URL = 'http://127.0.0.1:8000'; process.env.AUTH_ALLOWED_ORIGINS = 'http://localhost:3000';
});
afterEach(() => { globalThis.fetch = originalFetch; process.env = { ...originalEnv }; });
const request = (method = 'GET', extra: Record<string, string> = {}) => new Request('http://localhost:3000/api/workspaces/' + workspace + '/tasks', {
    method, headers: { Origin: 'http://localhost:3000', 'Content-Type': 'application/json', Cookie: 'private', ...extra }, ...(method === 'POST' ? { body: '{}' } : {}),
});
test('list forwards trusted credentials and copies only public fields', async () => {
    globalThis.fetch = async (url, init) => {
        assert.equal(String(url), `http://127.0.0.1:8000/workspaces/${workspace}/tasks?limit=20`);
        assert.equal(new Headers(init?.headers).get('cookie'), null);
        assert.equal(new Headers(init?.headers).get('X-Local-Runtime-Token'), 'd'.repeat(64));
        assert.equal(init?.redirect, 'error'); assert.equal(init?.cache, 'no-store');
        return Response.json({ items: [{ ...item, secret: 'PRIVATE' }], next_cursor: null });
    };
    const result = await taskProxy(request(), workspace);
    assert.equal(result.status, 200); assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await result.json(), { items: [item], next_cursor: null });
});
for (const raw of [{}, { items: [{ ...item, workspace_id: task }], next_cursor: null }, { items: [item, item], next_cursor: null }, { items: [], next_cursor: 'bad' }]) {
    test('invalid list is not an empty success ' + JSON.stringify(raw), async () => {
        globalThis.fetch = async () => Response.json(raw);
        assert.equal((await taskProxy(request(), workspace)).status, 502);
    });
}
for (const action of ['messages', 'title'] as const) {
    test(action + ' strips unknown fields', async () => {
        const expected = action === 'messages' ? { messages: [{ role: 'user', content: '正文' }] } : { title: '标题' };
        globalThis.fetch = async () => Response.json({ ...expected, secret: 'PRIVATE' });
        const response = await taskProxy(request(action === 'title' ? 'POST' : 'GET'), workspace, task, action);
        assert.equal(response.status, 200); assert.deepEqual(await response.json(), expected);
    });
    test(action + ' malformed payload is rejected', async () => {
        globalThis.fetch = async () => Response.json({ messages: [{ role: 'system', content: 'PRIVATE' }], title: '' });
        assert.equal((await taskProxy(request(action === 'title' ? 'POST' : 'GET'), workspace, task, action)).status, 502);
    });
}
for (const status of [403, 404, 422, 500]) {
    test('safe backend error ' + status, async () => {
        globalThis.fetch = async () => Response.json({ secret: 'PRIVATE' }, { status });
        const res = await taskProxy(request(), workspace);
        assert.equal(res.status, status === 500 ? 502 : status);
        assert.ok(!(await res.text()).includes('PRIVATE'));
    });
}
test('unsafe origin never forwards', async () => {
    globalThis.fetch = async () => { throw new Error('must not call'); };
    assert.equal((await taskProxy(request('POST', { Origin: 'https://evil.test' }), workspace, task, 'title')).status, 403);
});
test('network failure returns safe error without retry', async () => {
    let calls = 0; globalThis.fetch = async () => { calls++; throw new Error('PRIVATE'); };
    assert.equal((await taskProxy(request(), workspace)).status, 502); assert.equal(calls, 1);
});
