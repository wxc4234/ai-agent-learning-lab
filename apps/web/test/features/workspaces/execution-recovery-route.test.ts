import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { POST } from '../../../src/app/api/sessions/[sessionId]/execution/recover/route.ts';

const env = { ...process.env };
const id = 'a'.repeat(32);
const origin = 'http://localhost:3000';
function request(body: unknown = {}, headers: Record<string, string> = { Origin: origin }): Request {
    return new Request(`${origin}/api/sessions/${id}/execution/recover`, { method: 'POST', headers, body: JSON.stringify(body) });
}
const call = (req = request(), sessionId = id) => POST(req, { params: Promise.resolve({ sessionId }) });
beforeEach(() => {
    process.env.APP_MODE = 'local';
    process.env.API_BASE_URL = 'http://127.0.0.1:8000';
    process.env.LOCAL_RUNTIME_TOKEN = 'b'.repeat(64);
    process.env.AUTH_ALLOWED_ORIGINS = origin;
});
afterEach(() => { process.env = { ...env }; });

for (const status of [204, 403, 404, 409, 422, 500]) {
    test(`safe recovery response ${status}`, async t => {
        const mocked = t.mock.method(globalThis, 'fetch', async (_url: Parameters<typeof fetch>[0], options?: RequestInit) => {
            const headers = new Headers(options?.headers);
            assert.equal(headers.get('cookie'), null);
            assert.equal(headers.get('authorization'), null);
            assert.equal(headers.get('x-local-runtime-token'), 'b'.repeat(64));
            assert.equal(options?.body, '{}');
            return status === 204 ? new Response(null, { status, headers: { 'Set-Cookie': 'PRIVATE' } })
                : Response.json({ code: 'execution_recovery_refused', message: 'PRIVATE', owner_pid: 42 }, { status });
        });
        const res = await call();
        assert.equal(res.status, status === 500 ? 502 : status);
        assert.equal(res.headers.get('cache-control'), 'no-store');
        assert.equal(res.headers.get('set-cookie'), null);
        assert.ok(!(await res.text()).includes('PRIVATE'));
        assert.equal(mocked.mock.callCount(), 1);
    });
}
for (const body of [null, [], { force: true }, { owner_pid: 12 }]) {
    test(`reject request ${JSON.stringify(body)}`, async t => {
        const mocked = t.mock.method(globalThis, 'fetch', async () => { throw Error('must not call'); });
        assert.equal((await call(request(body))).status, 422);
        assert.equal(mocked.mock.callCount(), 0);
    });
}
test('missing origin and invalid id never forward', async t => {
    const mocked = t.mock.method(globalThis, 'fetch', async () => { throw Error('must not call'); });
    assert.equal((await call(request({}, {}))).status, 403);
    assert.equal((await call(request(), '../unsafe')).status, 422);
    assert.equal(mocked.mock.callCount(), 0);
});
test('unknown conflict and network loss remain uncertain, no automatic retry', async t => {
    const mocked = t.mock.method(globalThis, 'fetch', async () => Response.json({ code: 'conversation_busy' }, { status: 409 }));
    assert.equal((await call()).status, 502);
    mocked.mock.mockImplementation(async () => { throw Error('PRIVATE'); });
    assert.equal((await call()).status, 502);
    assert.equal(mocked.mock.callCount(), 2);
});
