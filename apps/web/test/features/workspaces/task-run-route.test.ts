import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { GET, runtime } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/runs/route.ts';

const originalEnv = { ...process.env };
const workspaceId = 'a'.repeat(32);
const taskId = 'b'.repeat(32);
const token = 'd'.repeat(64);
const browserOrigin = 'http://localhost:3000';
const backendOrigin = 'http://127.0.0.1:8000';
const path = `/workspaces/${workspaceId}/tasks/${taskId}/runs`;

function detail() {
    return {
        workspace_id: workspaceId, task_id: taskId,
        items: [{ run_id: 5, status: 'done', started_at: '2026-09-16T00:00:00Z',
            finished_at: '2026-09-16T00:00:01Z', duration_ms: 1000 }],
        next_cursor: null as string | null,
    };
}

function request(init: RequestInit = {}) {
    return new Request(`${browserOrigin}/api${path}`, init);
}

function route(req = request(), workspace = workspaceId, task = taskId) {
    return GET(req, { params: Promise.resolve({ workspaceId: workspace, taskId: task }) });
}

async function assertFailure(response: Response, status: number) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await response.json(), { message: '任务运行历史暂时不可用' });
    assert.equal(response.headers.get('set-cookie'), null);
}

beforeEach(() => {
    process.env.APP_MODE = 'local';
    process.env.API_BASE_URL = backendOrigin;
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = browserOrigin;
});

afterEach(() => {
    process.env = { ...originalEnv };
});

for (const [name, configure, req] of [
    ['account mode', () => { process.env.APP_MODE = 'account'; }, () => request()],
    ['invalid mode', () => { process.env.APP_MODE = 'invalid'; }, () => request()],
    ['missing token', () => { delete process.env.LOCAL_RUNTIME_TOKEN; }, () => request()],
    ['remote backend', () => { process.env.API_BASE_URL = 'http://example.com'; }, () => request()],
    ['remote host', () => {}, () => new Request(`http://example.com/api${path}`)],
    ['foreign origin', () => {}, () => request({ headers: { Origin: 'https://evil.test' } })],
    ['cross-site request', () => {}, () => request({ headers: { 'Sec-Fetch-Site': 'cross-site' } })],
] as const) {
    test(`local boundary rejects ${name}`, async (t) => {
        configure();
        const fetchMock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(req()), 403);
        assert.equal(fetchMock.mock.callCount(), 0);
    });
}

for (const status of [201, 204, 302, 400, 401, 403, 404, 422, 429, 500]) {
    test(`upstream ${status} is sanitized`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => new Response(
            status === 204 ? null : 'PRIVATE traceback and credentials',
            { status, headers: { 'Set-Cookie': 'PRIVATE' } },
        ));
        await assertFailure(await route(), [403, 404, 422].includes(status) ? status : 502);
    });
}

test('malformed JSON is sanitized', async (t) => {
    t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE invalid JSON'));
    await assertFailure(await route(), 502);
});

test('network failure is sanitized without retry', async (t) => {
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => { throw new Error('PRIVATE'); });
    await assertFailure(await route(), 502);
    assert.equal(fetchMock.mock.callCount(), 1);
});

test('already cancelled request never forwards', async (t) => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = t.mock.method(globalThis, 'fetch');
    await assertFailure(await route(request({ signal: controller.signal })), 502);
    assert.equal(fetchMock.mock.callCount(), 0);
});

// 用可控 signal 驱动超时，不等待真实 20 秒；同时断言生产超时预算。
for (const cause of ['browser cancellation', 'timeout'] as const) {
    for (const phase of ['fetch', 'body', 'after JSON'] as const) {
        test(`${cause} during ${phase} prevents a successful response`, async (t) => {
            const controller = new AbortController();
            if (cause === 'timeout') {
                t.mock.method(AbortSignal, 'timeout', (milliseconds: number) => {
                    assert.equal(milliseconds, 20_000);
                    return controller.signal;
                });
            }
            let forwardedSignal: AbortSignal | null | undefined;
            t.mock.method(globalThis, 'fetch', async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
                forwardedSignal = init?.signal;
                assert.ok(forwardedSignal);
                if (phase === 'fetch') {
                    return await new Promise<Response>((_resolve, reject) => {
                        forwardedSignal?.addEventListener('abort', () => reject(forwardedSignal?.reason), { once: true });
                        controller.abort(new Error('PRIVATE'));
                    });
                }
                if (phase === 'body') {
                    return new Response(new ReadableStream<Uint8Array>({
                        // highWaterMark=0 确保在 json() 开始消费时才触发取消。
                        pull(stream) {
                            forwardedSignal?.addEventListener('abort', () => stream.error(forwardedSignal?.reason), { once: true });
                            controller.abort(new Error('PRIVATE'));
                        },
                    }, { highWaterMark: 0 }));
                }
                const response = Response.json(detail());
                t.mock.method(response, 'json', async () => {
                    controller.abort(new Error('PRIVATE'));
                    return detail();
                });
                return response;
            });
            await assertFailure(await route(request(
                cause === 'browser cancellation' ? { signal: controller.signal } : {},
            )), 502);
            assert.equal(forwardedSignal?.aborted, true);
        });
    }
}

test('trusted headers, normalized pagination and public response only', async (t) => {
    const payload = detail();
    payload.next_cursor = '5';
    t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
        assert.equal(String(url), `${backendOrigin}${path}?limit=1&before=10`);
        assert.equal(init?.method, 'GET');
        assert.equal(init?.cache, 'no-store');
        assert.equal(init?.redirect, 'error');
        assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
        return Response.json({ ...payload, secret: 'PRIVATE', items: payload.items.map(
            item => ({ ...item, events: ['PRIVATE'], user_id: 1 }),
        ) }, { headers: { 'Set-Cookie': 'PRIVATE', 'X-Internal': 'PRIVATE' } });
    });
    const response = await route(new Request(`${browserOrigin}/api${path}?before=10&limit=1`, {
        headers: { Cookie: 'PRIVATE', Authorization: 'PRIVATE', 'X-Local-Runtime-Token': 'forged' },
    }));
    assert.equal(runtime, 'nodejs');
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('x-internal'), null);
    assert.deepEqual(await response.json(), payload);
});

for (const query of [
    'before=0', 'before=2147483648', 'before=-1', 'before=', 'before=01',
    'before=1.0', 'before=1e2', 'before=%0A1', 'before=1%0A', 'before=true',
    'limit=0', 'limit=51', 'limit=', 'limit=1.5', 'limit=01',
    'before=1&before=2', 'limit=1&limit=2', 'user_id=1',
]) {
    test(`invalid query ${query} never forwards`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(new Request(`${browserOrigin}/api${path}?${query}`)), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}

for (const [workspace, task] of [
    ['bad', taskId], [workspaceId, 'bad'], [workspaceId + '\n', taskId],
    [workspaceId, taskId.toUpperCase()],
]) {
    test(`invalid identifiers ${JSON.stringify([workspace, task])}`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(request(), workspace, task), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}

const invalid: Array<[string, () => unknown]> = [
    ['null', () => null],
    ['wrong project', () => ({ ...detail(), workspace_id: taskId })],
    ['wrong task', () => ({ ...detail(), task_id: workspaceId })],
    ['missing cursor', () => ({ ...detail(), next_cursor: undefined })],
    ['numeric cursor', () => ({ ...detail(), next_cursor: 5 })],
    ['cursor on short page', () => ({ ...detail(), next_cursor: '5' })],
    ['too many items', () => ({ ...detail(), items: Array(21).fill(detail().items[0]) })],
    ['duplicate IDs', () => ({ ...detail(), items: [detail().items[0], detail().items[0]] })],
    ['ascending IDs', () => ({ ...detail(), items: [detail().items[0], { ...detail().items[0], run_id: 6 }] })],
];
for (const [key, value] of [
    ['run_id', 0], ['run_id', 2147483648], ['run_id', '5'], ['run_id', 1.5],
    ['status', ''], ['status', null], ['started_at', 'bad'], ['started_at', null],
    ['finished_at', 'bad'], ['finished_at', '2025-01-01T00:00:00Z'],
    ['finished_at', null], ['duration_ms', null], ['duration_ms', -1],
    ['duration_ms', 1.5], ['duration_ms', Number.MAX_SAFE_INTEGER + 1],
] as const) {
    invalid.push([`${key} ${value}`, () => ({ ...detail(), items: [{ ...detail().items[0], [key]: value }] })]);
}
for (const [name, payload] of invalid) {
    test(`invalid response ${name}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(payload()));
        await assertFailure(await route(), 502);
    });
}

for (const [name, payload] of [
    ['empty', { ...detail(), items: [] }],
    ['unknown running state', { ...detail(), items: [{ ...detail().items[0], status: 'future', finished_at: null, duration_ms: null }] }],
    ['default page', detail()],
] as const) {
    test(`accepts ${name} and defaults to limit 20`, async (t) => {
        t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0]) => {
            assert.equal(String(url), `${backendOrigin}${path}?limit=20`);
            return Response.json(payload);
        });
        const response = await route();
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), payload);
    });
}

for (const payload of [
    { ...detail(), next_cursor: '4' },
    { ...detail(), next_cursor: '05' },
    { ...detail(), items: [{ ...detail().items[0], run_id: 10 }] },
]) {
    test(`rejects bad cursor boundary ${JSON.stringify(payload)}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(payload));
        await assertFailure(await route(new Request(`${browserOrigin}/api${path}?limit=1&before=10`)), 502);
    });
}
