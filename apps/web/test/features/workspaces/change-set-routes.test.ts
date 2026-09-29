import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { changeSetProxy } from '../../../src/app/api/_shared/change-set-proxy.ts';
import { ownedAreaProxy } from '../../../src/app/api/_shared/owned-area-proxy.ts';
const env = { ...process.env };
afterEach(() => { process.env = { ...env }; });
const w = 'a'.repeat(32), task = 'b'.repeat(32), id = 'c'.repeat(32), origin = 'http://localhost:3000';
for (const kind of ['changes', 'area']) for (const mode of ['read', 'write']) for (const failure of ['none', 'origin', 'extra', 'foreign', 'transport', 'aborted']) {
    test(`${kind} ${mode} ${failure}`, async t => {
        process.env.APP_MODE = 'local'; process.env.LOCAL_RUNTIME_TOKEN = 'e'.repeat(64); process.env.AUTH_ALLOWED_ORIGINS = origin;
        const item = { change_id: id, status: 'pending', paths: ['file.txt'], diff: 'diff', audit: [], secret: 'PRIVATE' };
        const area = { workspace_id: id, task_id: id, source_workspace_id: w, source_task_id: task, exported_change_id: null, secret: 'PRIVATE' };
        const raw = { workspace_id: failure === 'foreign' ? id : w, task_id: task,
            ...(kind === 'changes' ? mode === 'write' ? { item } : { items: [item] } : mode === 'write' ? { area } : { current: null, copies: [area] }), secret: 'PRIVATE' };
        const fetch = t.mock.method(globalThis, 'fetch', async (_url: unknown, init?: RequestInit) => {
            if (failure === 'transport') throw new Error('PRIVATE');
            assert.equal(init?.redirect, 'error'); return Response.json(raw);
        });
        const controller = new AbortController(); if (failure === 'aborted') controller.abort();
        const request = new Request(origin + '/api/test' + (failure === 'extra' && mode === 'read' ? '?root=PRIVATE' : ''), {
            method: mode === 'write' ? 'POST' : 'GET', signal: controller.signal,
            ...(mode === 'write' ? { headers: { Origin: failure === 'origin' ? 'http://bad' : origin, 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: kind === 'changes' ? 'approve' : 'create', ...(failure === 'extra' ? { root: 'PRIVATE' } : {}) }) } : {}),
        });
        const response = kind === 'changes' ? await changeSetProxy(request, w, task, mode === 'write' ? id : undefined) : await ownedAreaProxy(request, w, task);
        const expected = failure === 'extra' ? 422 : failure === 'origin' && mode === 'write' ? 403 : failure === 'foreign' || failure === 'transport' ? 502 : failure === 'aborted' ? 499 : 200;
        assert.equal(response.status, expected);
        assert.equal(fetch.mock.callCount(), [422,403,499].includes(expected) ? 0 : 1);
        assert.ok(!(await response.text()).includes('PRIVATE'));
    });
}
