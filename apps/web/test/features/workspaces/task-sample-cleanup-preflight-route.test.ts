import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { readTaskSampleCleanupPreflight } from '../../../src/features/workbench/task-sample-cleanup-preflight-data.ts';
import { GET, runtime } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/sample-cleanup-preflight/route.ts';

const originalEnv = { ...process.env };
const workspaceId = 'a'.repeat(32);
const taskId = 'b'.repeat(32);
const token = 'c'.repeat(64);
const browserOrigin = 'http://localhost:3000';
const backendOrigin = 'http://127.0.0.1:8000';
const path = `/workspaces/${workspaceId}/tasks/${taskId}/sample-cleanup-preflight`;
const error = {
    code: 'sample_cleanup_preflight_read_failed',
    message: '读取样例清理诊断失败，不能据此判断目录状态',
};
const results = [
    'evidence_missing',
    'not_pending',
    'evidence_inconsistent',
    'directory_missing',
    'identity_unverifiable',
    'identity_matches_record',
    'inspection_unavailable',
] as const;

function snapshot(result: typeof results[number] = 'identity_matches_record') {
    return {
        workspace_id: workspaceId,
        task_id: taskId,
        result,
    };
}

function request(init: RequestInit = {}): Request {
    return new Request(`${browserOrigin}/api${path}`, init);
}

function route(req = request(), workspace = workspaceId, task = taskId): Promise<Response> {
    return GET(req, { params: Promise.resolve({ workspaceId: workspace, taskId: task }) });
}

async function assertFailure(response: Response, status: number): Promise<void> {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.deepEqual(await response.json(), error);
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

for (const result of results) {
    test(`GET projects only the public ${result} diagnosis`, async (t) => {
        const fetchMock = t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `${backendOrigin}${path}`);
            assert.equal(init?.method, 'GET');
            assert.equal(init?.cache, 'no-store');
            assert.equal(init?.redirect, 'error');
            assert.equal(init?.body, undefined);
            assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
            return Response.json({
                ...snapshot(result),
                root_path: '/PRIVATE/workspace',
                root_dev: 123,
                root_ino: 456,
                sample_handle: 'PRIVATE',
            }, { headers: { 'Set-Cookie': 'PRIVATE', 'X-Internal': 'PRIVATE' } });
        });

        const response = await route(request({
            headers: { Cookie: 'PRIVATE', Authorization: 'PRIVATE', 'X-Local-Runtime-Token': 'forged' },
        }));
        assert.equal(runtime, 'nodejs');
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('cache-control'), 'no-store');
        assert.equal(response.headers.get('set-cookie'), null);
        assert.equal(response.headers.get('x-internal'), null);
        assert.deepEqual(await response.json(), snapshot(result));
        assert.equal(fetchMock.mock.callCount(), 1);
    });
}

for (const [name, configure, req] of [
    ['account mode', () => { process.env.APP_MODE = 'account'; }, () => request()],
    ['invalid mode', () => { process.env.APP_MODE = 'invalid'; }, () => request()],
    ['missing token', () => { delete process.env.LOCAL_RUNTIME_TOKEN; }, () => request()],
    ['remote backend', () => { process.env.API_BASE_URL = 'http://example.com'; }, () => request()],
    ['remote host', () => {}, () => new Request(`http://example.com/api${path}`)],
    ['foreign origin', () => {}, () => request({ headers: { Origin: 'https://evil.test' } })],
    ['cross-site request', () => {}, () => request({ headers: { 'Sec-Fetch-Site': 'cross-site' } })],
] as const) {
    test(`local access rejects ${name} before forwarding`, async (t) => {
        configure();
        const fetchMock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(req()), 403);
        assert.equal(fetchMock.mock.callCount(), 0);
    });
}

for (const field of ['workspace', 'task'] as const) {
    for (const invalid of ['bad', 'A'.repeat(32), 'a'.repeat(32) + '\n']) {
        test(`invalid ${field} path identifier is rejected`, async (t) => {
            const fetchMock = t.mock.method(globalThis, 'fetch');
            await assertFailure(await route(request(), field === 'workspace' ? invalid : workspaceId,
                field === 'task' ? invalid : taskId), 422);
            assert.equal(fetchMock.mock.callCount(), 0);
        });
    }
}

for (const query of ['user_id=1', 'result=directory_missing', 'x=', 'x=1&x=2']) {
    test(`query ${query} is rejected without forwarding`, async (t) => {
        const fetchMock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(new Request(`${browserOrigin}/api${path}?${query}`)), 422);
        assert.equal(fetchMock.mock.callCount(), 0);
    });
}

test('request body and body declarations are rejected without forwarding', async (t) => {
    const fetchMock = t.mock.method(globalThis, 'fetch');
    await assertFailure(await route(request({ method: 'POST', body: '{}' })), 422);
    await assertFailure(await route(request({ headers: { 'Content-Length': '2' } })), 422);
    await assertFailure(await route(request({ headers: { 'Transfer-Encoding': 'chunked' } })), 422);
    assert.equal(fetchMock.mock.callCount(), 0);
});

for (const [field, value] of [
    ['workspace_id', taskId],
    ['task_id', workspaceId],
    ['result', 'unknown'],
    ['result', null],
    ['result', true],
    ['result', 'IDENTITY_MATCHES_RECORD'],
] as const) {
    test(`invalid upstream ${field} is a diagnosis read failure`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ ...snapshot(), [field]: value }));
        await assertFailure(await route(), 502);
    });
}

test('parser rejects missing fields, prototypes and nonobjects', () => {
    for (const raw of [null, [], true, 'identity_matches_record', 42]) {
        assert.equal(readTaskSampleCleanupPreflight(raw, workspaceId, taskId), null);
    }
    for (const field of Object.keys(snapshot())) {
        assert.equal(readTaskSampleCleanupPreflight({ ...snapshot(), [field]: undefined }, workspaceId, taskId), null);
    }
    assert.equal(readTaskSampleCleanupPreflight(Object.create(snapshot()), workspaceId, taskId), null);
});

for (const upstreamStatus of [204, 302, 400, 401, 403, 404, 422, 429, 500]) {
    test(`upstream ${upstreamStatus} is sanitized`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => new Response(
            upstreamStatus === 204 ? null : 'PRIVATE traceback',
            { status: upstreamStatus, headers: { 'Set-Cookie': 'PRIVATE' } },
        ));
        await assertFailure(await route(), [403, 404, 422].includes(upstreamStatus) ? upstreamStatus : 502);
    });
}

test('unused upstream error body is cancelled', async (t) => {
    let cancelled = false;
    t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
        cancel() { cancelled = true; },
    }), { status: 404 }));
    await assertFailure(await route(), 404);
    assert.equal(cancelled, true);
});

test('invalid JSON is sanitized without retry', async (t) => {
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE invalid JSON'));
    await assertFailure(await route(), 502);
    assert.equal(fetchMock.mock.callCount(), 1);
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

for (const cause of ['browser cancellation', 'timeout'] as const) {
    for (const phase of ['fetch', 'body', 'after JSON'] as const) {
        test(`${cause} during ${phase} cannot return a diagnosis`, async (t) => {
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
                        pull(stream) {
                            forwardedSignal?.addEventListener('abort', () => stream.error(forwardedSignal?.reason), { once: true });
                            controller.abort(new Error('PRIVATE'));
                        },
                    }, { highWaterMark: 0 }));
                }
                const response = Response.json(snapshot());
                t.mock.method(response, 'json', async () => {
                    controller.abort(new Error('PRIVATE'));
                    return snapshot();
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
