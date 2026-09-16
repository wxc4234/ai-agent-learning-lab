import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { GET, runtime } from '../../../src/app/api/runs/[runId]/route.ts';

const originalEnv = { ...process.env };
const token = 'd'.repeat(64);
const browserOrigin = 'http://localhost:3000';
const backendOrigin = 'http://127.0.0.1:8000';
const path = '/runs/5';

function detail() {
    return { run_id: 5, status: 'done', started_at: '2026-09-16T00:00:00Z',
        finished_at: '2026-09-16T00:00:01Z', duration_ms: 1000,
        events: [{ id: 1, event_type: 'TEXT_MESSAGE_CONTENT',
            created_at: '2026-09-16T00:00:00Z', payload: { chunk: 'hello' } }] };
}
function request(init: RequestInit = {}) {
    return new Request(`${browserOrigin}/api${path}`, init);
}
function route(req = request(), runId = '5') {
    return GET(req, { params: Promise.resolve({ runId }) });
}
async function assertFailure(response: Response, status: number) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await response.json(), { message: '运行详情暂时不可用' });
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


test('forwards only trusted credentials and strips internal fields', async (t) => {
    t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
        assert.equal(String(url), `${backendOrigin}${path}`);
        assert.equal(init?.method, 'GET');
        assert.equal(init?.cache, 'no-store');
        assert.equal(init?.redirect, 'error');
        assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
        return Response.json({ ...detail(), secret: 'PRIVATE', events: detail().events.map(e => ({
            ...e, owner: 'PRIVATE', payload: { ...e.payload, type: 'RUN_FINISHED', secret: 'PRIVATE' },
        })) }, { headers: { 'Set-Cookie': 'PRIVATE' } });
    });
    const response = await route(request({ headers: { Cookie: 'PRIVATE', 'X-Local-Runtime-Token': 'forged' } }));
    assert.equal(runtime, 'nodejs');
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.deepEqual(await response.json(), detail());
});
for (const id of ['0', '-1', '01', '1.0', '1e2', '2147483648', '5\n', '../5', '']) {
    test(`invalid run id ${JSON.stringify(id)}`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(request(), id), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}
test('rejects query parameters before fetch', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch');
    await assertFailure(await route(new Request(`${browserOrigin}/api${path}?user_id=1`)), 422);
    assert.equal(mock.mock.callCount(), 0);
});
const invalid: Array<[string, unknown]> = [
    ['null', null], ['wrong id', { ...detail(), run_id: 6 }],
    ['bad status', { ...detail(), status: null }],
    ['bad time', { ...detail(), started_at: 'bad' }],
    ['negative duration', { ...detail(), duration_ms: -1 }],
    ['unfinished duration', { ...detail(), finished_at: null }],
    ['no events', { ...detail(), events: null }],
    ['duplicate event', { ...detail(), events: [...detail().events, ...detail().events] }],
];
for (const [field, value] of [
    ['id', 0], ['id', 1.5], ['created_at', 'bad'], ['payload', []],
    ['event_type', ''], ['payload', { chunk: 5 }],
] as const) invalid.push([field + String(value), { ...detail(), events: [{ ...detail().events[0], [field]: value }] }]);
for (const [name, data] of invalid) {
    test(`invalid detail ${name}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(data));
        await assertFailure(await route(), 502);
    });
}
for (const [event_type, payload, expected] of [
    ['RUN_STARTED', { session_id: 'abc', prompt_length: 0, secret: 'PRIVATE' }, { session_id: 'abc', prompt_length: 0 }],
    ['RUN_CANCELLATION_REQUESTED', { reason: 'user', secret: 'PRIVATE' }, { reason: 'user' }],
    ['RUN_ABORTED', { reason: 'unknown' }, { reason: 'unknown' }],
    ['RUN_ERROR', { reason: 'timeout' }, { reason: 'timeout' }],
    ['RUN_ERROR', { code: 'model_unavailable', message: '模型不可用', secret: 'PRIVATE' }, { code: 'model_unavailable', message: '模型不可用' }],
    ['FUTURE_EVENT', { secret: 'PRIVATE' }, {}],
    ['TOOL_CALL_START', { tool_call_id: '1', tool_name: 'search', arguments: '{}' }, { tool_call_id: '1', tool_name: 'search', arguments: '{}' }],
] as const) {
    test(`persisted ${event_type} public payload`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ ...detail(), events: [{ ...detail().events[0], event_type, payload }] }));
        const response = await route();
        assert.equal(response.status, 200);
        assert.deepEqual((await response.json()).events[0].payload, expected);
    });
}

for (const malformed of [false, true]) {
    test(`terminal metrics validated and rebuilt: malformed=${malformed}`, async (t) => {
        const payload = { steps_taken: 2, metrics: {
            model_usage: null, model_duration_ms: null, tool_duration_ms: 0,
            estimated_cost_cny: null,
            pricing: { model: 'test', tier: 'peak', cache_hit_input_cny_per_million: '0.10',
                cache_miss_input_cny_per_million: '3.0', output_cny_per_million: '9.0' },
        } };
        t.mock.method(globalThis, 'fetch', async () => Response.json({ ...detail(), events: [{
            ...detail().events[0], event_type: 'RUN_FINISHED', payload: {
                ...payload, metrics: { ...payload.metrics, tool_duration_ms: malformed ? -1 : 0, secret: 'PRIVATE',
                    pricing: { ...payload.metrics.pricing, secret: 'PRIVATE' } },
            },
        }] }));
        const response = await route();
        if (malformed) await assertFailure(response, 502);
        else {
            assert.equal(response.status, 200);
            assert.deepEqual((await response.json()).events[0].payload, payload);
        }
    });
}
test('unknown state with unfinished run and empty timeline is preserved', async (t) => {
    const payload = { ...detail(), status: 'future', finished_at: null, duration_ms: null, events: [] };
    t.mock.method(globalThis, 'fetch', async () => Response.json(payload));
    assert.deepEqual(await (await route()).json(), payload);
});
