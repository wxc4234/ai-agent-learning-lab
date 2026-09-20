import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { DELETE } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/route.ts';

const env = { ...process.env };
const origin = 'http://localhost:3000';
const workspaceId = 'a'.repeat(32);
const taskId = 'b'.repeat(32);
const path = `/workspaces/${workspaceId}/tasks/${taskId}`;
const token = 'd'.repeat(64);
function request(init: RequestInit = {}) {
    return new Request(`${origin}/api${path}`, { method: 'DELETE', headers: { Origin: origin }, ...init });
}
function route(req = request(), workspace = workspaceId, task = taskId) {
    return DELETE(req, { params: Promise.resolve({ workspaceId: workspace, taskId: task }) });
}
async function failure(response: Response, status: number, code: string) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    const body = await response.json();
    assert.equal(body.code, code);
    assert.deepEqual(Object.keys(body).sort(), ['code', 'message']);
    assert.ok(!JSON.stringify(body).includes('PRIVATE'));
}
beforeEach(() => {
    process.env.APP_MODE = 'local';
    process.env.API_BASE_URL = 'http://127.0.0.1:8000';
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...env }; });

test('204 empty; forwards trusted headers only and never parses success JSON', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
        assert.equal(String(url), `http://127.0.0.1:8000${path}`);
        assert.equal(init?.method, 'DELETE');
        assert.equal(init?.body, undefined);
        assert.equal(init?.cache, 'no-store');
        assert.equal(init?.redirect, 'error');
        assert.ok(init?.signal instanceof AbortSignal);
        assert.deepEqual([...new Headers(init?.headers)], [['origin', origin], ['x-local-runtime-token', token]]);
        const response = new Response(null, { status: 204, headers: { 'Set-Cookie': 'PRIVATE' } });
        t.mock.method(response, 'json', () => { throw Error('must not read'); });
        return response;
    });
    const response = await route(request({ headers: { Origin: origin, Cookie: 'PRIVATE', Authorization: 'PRIVATE', 'X-Local-Runtime-Token': 'PRIVATE' } }));
    assert.equal(mock.mock.callCount(), 1);
    assert.equal(response.status, 204);
    assert.equal(await response.text(), '');
    assert.deepEqual([...response.headers], [['cache-control', 'no-store']]);
});

// 输入失败必须在 fetch 前拒绝，不能产生删除副作用。
for (const [name, setup, req, status, code] of [
    ['account', () => { process.env.APP_MODE = 'account'; }, () => request(), 403, 'local_mode_required'],
    ['bad token', () => { process.env.LOCAL_RUNTIME_TOKEN = 'invalid'; }, () => request(), 403, 'local_access_rejected'],
    ['remote backend', () => { process.env.API_BASE_URL = 'https://example.com'; }, () => request(), 403, 'local_access_rejected'],
    ['missing origin', () => {}, () => request({ headers: {} }), 403, 'workspace_origin_rejected'],
    ['foreign origin', () => {}, () => request({ headers: { Origin: 'https://example.com' } }), 403, 'local_access_rejected'],
    ['cross site', () => {}, () => request({ headers: { Origin: origin, 'Sec-Fetch-Site': 'cross-site' } }), 403, 'local_access_rejected'],
    ['body', () => {}, () => request({ body: '{}' }), 422, 'invalid_task_input'],
    ['whitespace', () => {}, () => request({ body: ' ' }), 422, 'invalid_task_input'],
] as const) {
    test(`reject ${name}`, async (t) => {
        setup();
        const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('not expected'); });
        await failure(await route(req()), status, code);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [workspace, task] of [['../x', taskId], [workspaceId, 'B'.repeat(32)], ['', taskId]]) {
    test(`invalid IDs ${workspace}/${task}`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('not expected'); });
        await failure(await route(request(), workspace, task), 422, 'invalid_task_input');
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [status, code] of [[403, 'local_mode_required'], [403, 'local_access_rejected'], [403, 'workspace_origin_rejected'], [404, 'workspace_not_accessible'], [409, 'task_run_unsettled'], [409, 'conversation_busy'], [422, 'invalid_task_input'], [500, 'task_deletion_uncertain']] as const) {
    test(`safe mapping ${status}/${code}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ code, message: 'PRIVATE', internal: 'PRIVATE' }, { status, headers: { 'Set-Cookie': 'PRIVATE' } }));
        await failure(await route(), status, code);
    });
}
for (const [status, payload] of [[200, {}], [202, {}], [500, { code: 'conversation_busy' }], [404, { code: 'conversation_busy' }], [404, { code: 'task_run_unsettled' }], [409, { code: 'toString' }], [500, null], [502, { code: 'task_deletion_uncertain' }], [409, []]] as const) {
    test(`unknown ${status}/${JSON.stringify(payload)}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(payload, { status }));
        await failure(await route(), 502, 'task_deletion_uncertain');
    });
}
test('malformed JSON uncertain', async (t) => {
    t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE', { status: 500 }));
    await failure(await route(), 502, 'task_deletion_uncertain');
});
test('network failure uncertain without retry', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('PRIVATE'); });
    await failure(await route(), 502, 'task_deletion_uncertain');
    assert.equal(mock.mock.callCount(), 1);
});
test('cancel before forwarding not submitted', async (t) => {
    const controller = new AbortController();
    controller.abort();
    const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('unexpected'); });
    await failure(await route(request({ signal: controller.signal })), 499, 'task_request_cancelled');
    assert.equal(mock.mock.callCount(), 0);
});
for (const phase of ['fetch', 'body', 'after-json', 'success'] as const) {
    for (const kind of ['cancel', 'timeout'] as const) {
        test(`${kind} during ${phase} uncertain`, async (t) => {
            const browser = new AbortController();
            const timeout = new AbortController();
            t.mock.method(AbortSignal, 'timeout', () => timeout.signal);
            const abort = () => (kind === 'cancel' ? browser : timeout).abort();
            t.mock.method(globalThis, 'fetch', async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
                if (phase === 'fetch') { abort(); init?.signal?.throwIfAborted(); }
                const response = phase === 'success' ? new Response(null, { status: 204 }) : Response.json({ code: 'task_run_unsettled' }, { status: 409 });
                if (phase === 'success') abort();
                if (phase === 'body' || phase === 'after-json') {
                    t.mock.method(response, 'json', async () => {
                        abort();
                        if (phase === 'body') init?.signal?.throwIfAborted();
                        return { code: 'task_run_unsettled' };
                    });
                }
                return response;
            });
            await failure(await route(request({ signal: browser.signal })), kind === 'cancel' ? 499 : 504, 'task_deletion_uncertain');
        });
    }
}

for (const contentType of ['application/json', 'text/plain']) {
    test(`empty request accepts ${contentType}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }));
        assert.equal((await route(request({ headers: { Origin: origin, 'Content-Type': contentType } }))).status, 204);
    });
}
test('nonlocal browser host rejected before forwarding', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('unexpected'); });
    await failure(await route(new Request(`http://example.com/api${path}`, { method: 'DELETE', headers: { Origin: origin } })), 403, 'local_access_rejected');
    assert.equal(mock.mock.callCount(), 0);
});
for (const cancelled of [false, true]) {
    test(`request body read failure cancelled=${cancelled} never forwards`, async (t) => {
        const controller = new AbortController();
        const req = request({ signal: controller.signal });
        t.mock.method(req, 'arrayBuffer', async () => {
            if (cancelled) controller.abort();
            throw Error('PRIVATE');
        });
        const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('unexpected'); });
        await failure(await route(req), cancelled ? 499 : 400, cancelled ? 'task_request_cancelled' : 'invalid_task_request');
        assert.equal(mock.mock.callCount(), 0);
    });
}
test('cancel after reading request body never forwards', async (t) => {
    const controller = new AbortController();
    const req = request({ signal: controller.signal });
    t.mock.method(req, 'arrayBuffer', async () => { controller.abort(); return new ArrayBuffer(0); });
    const mock = t.mock.method(globalThis, 'fetch', async () => { throw Error('unexpected'); });
    await failure(await route(req), 499, 'task_request_cancelled');
    assert.equal(mock.mock.callCount(), 0);
});
