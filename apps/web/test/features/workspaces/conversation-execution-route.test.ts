import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { GET, runtime } from '../../../src/app/api/sessions/[sessionId]/execution/route.ts';

const env = { ...process.env };
const id = 'a'.repeat(32);
const token = 'd'.repeat(64);
const origin = 'http://localhost:3000';
const backend = 'http://127.0.0.1:8000';
const path = `/sessions/${id}/execution`;
const idle = { session_id: id, occupied: false, acquired_at: null };
const busy = { session_id: id, occupied: true, acquired_at: '2000-01-01T00:00:00Z' };
function request(init: RequestInit = {}) { return new Request(`${origin}/api${path}`, init); }
function route(req = request(), sessionId = id) { return GET(req, { params: Promise.resolve({ sessionId }) }); }
async function failure(response: Response, status: number) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.deepEqual(await response.json(), { message: '会话执行状态暂时不可用' });
}
beforeEach(() => {
    process.env.APP_MODE = 'local';
    process.env.API_BASE_URL = backend;
    process.env.LOCAL_RUNTIME_TOKEN = token;
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...env }; });

for (const payload of [idle, busy, { ...busy, acquired_at: '2026-09-20T08:00:00.123456+08:00' }]) {
    test(`public response ${JSON.stringify(payload)}`, async (t) => {
        const fetchMock = t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
            assert.equal(String(url), `${backend}${path}`);
            assert.equal(init?.method, 'GET');
            assert.equal(init?.body, undefined);
            assert.equal(init?.cache, 'no-store');
            assert.equal(init?.redirect, 'error');
            assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
            return Response.json({ ...payload, owner_token: 'PRIVATE' }, { headers: { 'Set-Cookie': 'PRIVATE', 'X-Internal': 'PRIVATE' } });
        });
        const response = await route(request({ headers: { Cookie: 'PRIVATE', Authorization: 'PRIVATE', 'X-Local-Runtime-Token': 'forged' } }));
        assert.equal(runtime, 'nodejs');
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('cache-control'), 'no-store');
        assert.equal(response.headers.get('set-cookie'), null);
        assert.equal(response.headers.get('x-internal'), null);
        assert.deepEqual(await response.json(), payload);
        assert.equal(fetchMock.mock.callCount(), 1);
    });
}
for (const [name, configure, req] of [
    ['account', () => { process.env.APP_MODE = 'account'; }, () => request()],
    ['bad mode', () => { process.env.APP_MODE = 'bad'; }, () => request()],
    ['missing token', () => { delete process.env.LOCAL_RUNTIME_TOKEN; }, () => request()],
    ['remote backend', () => { process.env.API_BASE_URL = 'https://example.com'; }, () => request()],
    ['remote host', () => {}, () => new Request(`http://example.com/api${path}`)],
    ['foreign origin', () => {}, () => request({ headers: { Origin: 'https://evil.test' } })],
    ['cross site', () => {}, () => request({ headers: { 'Sec-Fetch-Site': 'cross-site' } })],
] as const) {
    test(`boundary ${name}`, async (t) => {
        configure();
        const mock = t.mock.method(globalThis, 'fetch', async () => Response.json(idle));
        await failure(await route(req()), 403);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const value of ['', 'a'.repeat(31), 'a'.repeat(33), 'A'.repeat(32), 'g'.repeat(32), '../x', `${id}\n`, `${id}/x`, '%2e%2e']) {
    test(`invalid id ${JSON.stringify(value)}`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch', async () => Response.json(idle));
        await failure(await route(request(), value), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const query of ['?user_id=1', '?before=1', '?unexpected=']) {
    test(`reject query ${query}`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch', async () => Response.json(idle));
        await failure(await route(new Request(`${origin}/api${path}${query}`)), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const status of [201, 204, 302, 400, 401, 403, 404, 409, 422, 429, 500, 503]) {
    test(`safe upstream ${status}`, async (t) => {
        let cancelled = false;
        const upstream = new Response(status === 204 ? null : 'PRIVATE', { status, headers: { 'Set-Cookie': 'PRIVATE' } });
        if (upstream.body) t.mock.method(upstream.body, 'cancel', async () => { cancelled = true; });
        t.mock.method(upstream, 'json', async () => { assert.fail('must not read error JSON'); });
        t.mock.method(upstream, 'text', async () => { assert.fail('must not read error text'); });
        const mock = t.mock.method(globalThis, 'fetch', async () => upstream);
        await failure(await route(), [403, 404, 422].includes(status) ? status : 502);
        assert.equal(cancelled, status !== 204);
        assert.equal(mock.mock.callCount(), 1);
    });
}
test('discard failure keeps safe status', async (t) => {
    const upstream = new Response('PRIVATE', { status: 404 });
    t.mock.method(upstream.body!, 'cancel', async () => { throw new Error('PRIVATE'); });
    t.mock.method(globalThis, 'fetch', async () => upstream);
    await failure(await route(), 404);
});
const invalid: Array<[string, unknown]> = [
    ['null', null], ['array', []], ['missing', {}], ['wrong id', { ...idle, session_id: 'b'.repeat(32) }],
    ['string boolean', { ...idle, occupied: 'false' }], ['number boolean', { ...idle, occupied: 0 }],
    ['missing time', { session_id: id, occupied: false }],
    ['idle with time', { ...idle, acquired_at: busy.acquired_at }], ['busy no time', { ...busy, acquired_at: null }],
];
for (const time of ['', 'bad', '2026-09-20', '2026-09-20T00:00:00', '2026-99-99T00:00:00Z', `${busy.acquired_at}\n`, 42]) {
    invalid.push([`bad time ${JSON.stringify(time)}`, { ...busy, acquired_at: time }]);
}
for (const [name, value] of invalid) {
    test(`invalid response ${name}`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json(value));
        await failure(await route(), 502);
    });
}
test('invalid JSON', async (t) => {
    t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE invalid JSON'));
    await failure(await route(), 502);
});
test('network failure without retry', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch', async () => { throw new Error('PRIVATE'); });
    await failure(await route(), 502);
    assert.equal(mock.mock.callCount(), 1);
});
test('already cancelled request skips fetch', async (t) => {
    const controller = new AbortController();
    controller.abort();
    const mock = t.mock.method(globalThis, 'fetch', async () => Response.json(idle));
    await failure(await route(request({ signal: controller.signal })), 502);
    assert.equal(mock.mock.callCount(), 0);
});
// 控制信号触发超时，不等待真实 20 秒；同时断言生产预算和两个读取阶段。
for (const cause of ['client', 'timeout']) {
    for (const phase of ['fetch', 'body', 'after JSON']) {
        test(`${cause} cancellation at ${phase}`, async (t) => {
            const controller = new AbortController();
            if (cause === 'timeout') t.mock.method(AbortSignal, 'timeout', (ms: number) => {
                assert.equal(ms, 20_000);
                return controller.signal;
            });
            const mock = t.mock.method(globalThis, 'fetch', async (_url: Parameters<typeof fetch>[0], init?: RequestInit) => {
                const signal = init?.signal;
                assert.ok(signal);
                if (phase === 'fetch') return await new Promise<Response>((_resolve, reject) => {
                    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
                    controller.abort(new Error('PRIVATE'));
                });
                if (phase === 'body') return new Response(new ReadableStream<Uint8Array>({
                    pull(stream) {
                        signal.addEventListener('abort', () => stream.error(signal.reason), { once: true });
                        controller.abort(new Error('PRIVATE'));
                    },
                }, { highWaterMark: 0 }));
                const response = Response.json(idle);
                t.mock.method(response, 'json', async () => {
                    controller.abort(new Error('PRIVATE'));
                    return idle;
                });
                return response;
            });
            await failure(await route(request(cause === 'client' ? { signal: controller.signal } : {})), 502);
            assert.equal(mock.mock.callCount(), 1);
        });
    }
}
