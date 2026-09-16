import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { GET, runtime } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/route.ts';
import { taskProxy } from '../../../src/app/api/_shared/task-proxy.ts';
import { readTaskDetail } from '../../../src/features/workbench/task-data.ts';

const originalEnv = { ...process.env };
const workspaceId = 'a'.repeat(32);
const taskId = 'b'.repeat(32);
const token = 'd'.repeat(64);
const browserOrigin = 'http://localhost:3000';
const backendOrigin = 'http://127.0.0.1:8000';
const path = `/workspaces/${workspaceId}/tasks/${taskId}`;

function detail() {
    return {
        workspace: {
            external_id: workspaceId,
            name: '学习项目',
            created_at: '2026-09-15T00:00:00Z',
        },
        task: {
            external_id: taskId,
            workspace_id: workspaceId,
            conversation_id: 'c'.repeat(32),
            title: '详情 BFF',
            created_at: '2026-09-16T00:00:00Z',
        },
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
    assert.deepEqual(await response.json(), { message: '任务服务暂时不可用' });
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

test('detail route forwards trusted credentials and returns only public fields', async (t) => {
    const payload = detail();
    const fetchMock = t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
        assert.equal(String(url), `${backendOrigin}${path}`);
        assert.equal(init?.method, 'GET');
        assert.equal(init?.body, undefined);
        assert.equal(init?.cache, 'no-store');
        assert.equal(init?.redirect, 'error');
        assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
        assert.ok(init?.signal instanceof AbortSignal);
        return Response.json({
            ...payload,
            secret: 'PRIVATE',
            workspace: { ...payload.workspace, id: 1, user_id: 2, root_path: '/PRIVATE' },
            task: { ...payload.task, id: 3, user_id: 2 },
        }, { headers: { 'Set-Cookie': 'PRIVATE', 'X-Internal': 'PRIVATE' } });
    });
    const response = await route(request({ headers: {
        Origin: browserOrigin,
        Cookie: 'PRIVATE',
        Authorization: 'Bearer PRIVATE',
        'X-Local-Runtime-Token': 'browser-forged-token',
    } }));
    assert.equal(runtime, 'nodejs');
    assert.equal(fetchMock.mock.callCount(), 1);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('x-internal'), null);
    assert.deepEqual(await response.json(), payload);
});

test('same-origin GET may omit Origin', async (t) => {
    t.mock.method(globalThis, 'fetch', async () => Response.json(detail()));
    assert.equal((await route()).status, 200);
});

// 每个畸形成功响应都经过真实路由和解析器，不能只测 TypeScript 类型。
const invalidPayloads: Array<[string, () => unknown]> = [
    ['null body', () => null],
    ['array body', () => []],
    ['missing workspace', () => ({ task: detail().task })],
    ['workspace array', () => ({ ...detail(), workspace: [] })],
    ['missing task', () => ({ workspace: detail().workspace })],
    ['task array', () => ({ ...detail(), task: [] })],
];

for (const [field, value] of [
    ['external_id', 'e'.repeat(32)],
    ['external_id', workspaceId.toUpperCase()],
    ['name', '   '],
    ['name', null],
    ['name', '名'.repeat(101)],
    ['created_at', 'invalid-date'],
    ['created_at', 123],
] as const) {
    invalidPayloads.push([`workspace ${field} ${String(value)}`, () => ({
        ...detail(), workspace: { ...detail().workspace, [field]: value },
    })]);
}

for (const [field, value] of [
    ['external_id', 'e'.repeat(32)],
    ['external_id', taskId.toUpperCase()],
    ['workspace_id', 'e'.repeat(32)],
    ['conversation_id', 'invalid'],
    ['conversation_id', 'C'.repeat(32)],
    ['conversation_id', null],
    ['title', '   '],
    ['title', 123],
    ['title', '题'.repeat(201)],
    ['created_at', 'invalid-date'],
    ['created_at', null],
] as const) {
    invalidPayloads.push([`task ${field} ${String(value)}`, () => ({
        ...detail(), task: { ...detail().task, [field]: value },
    })]);
}

for (const [name, payload] of invalidPayloads) {
    test(`invalid detail: ${name}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(payload()));
        await assertFailure(await route(), 502);
    });
}

test('Unicode names and titles use code point length at the limit', () => {
    const payload = detail();
    payload.workspace.name = '😀'.repeat(100);
    payload.task.title = '😀'.repeat(200);
    assert.deepEqual(readTaskDetail(payload, workspaceId, taskId), payload);
    payload.workspace.name += '😀';
    assert.equal(readTaskDetail(payload, workspaceId, taskId), null);
    payload.workspace.name = '项目';
    payload.task.title += '😀';
    assert.equal(readTaskDetail(payload, workspaceId, taskId), null);
});

for (const [workspace, task] of [
    ['bad', taskId],
    [workspaceId, 'bad'],
    [workspaceId.toUpperCase(), taskId],
    [workspaceId, '../messages'],
]) {
    test(`invalid URL identifiers: ${workspace}/${task}`, async (t) => {
        const fetchMock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(request(), workspace, task), 422);
        assert.equal(fetchMock.mock.callCount(), 0);
        assert.equal(readTaskDetail(detail(), workspace, task), null);
    });
}

test('detail action without taskId fails before forwarding', async (t) => {
    const fetchMock = t.mock.method(globalThis, 'fetch');
    await assertFailure(await taskProxy(request(), workspaceId, undefined, 'detail'), 422);
    assert.equal(fetchMock.mock.callCount(), 0);
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
