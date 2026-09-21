import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { readFileEditProposalDetail } from '../../../src/features/workbench/file-edit-proposal-data.ts';
import { GET, runtime } from '../../../src/app/api/workspaces/[workspaceId]/tasks/[taskId]/file-edit-proposals/[proposalId]/route.ts';

const originalEnv = { ...process.env };
const workspaceId = 'a'.repeat(32);
const taskId = 'b'.repeat(32);
const proposalId = 'c'.repeat(32);
const token = 'd'.repeat(64);
const browserOrigin = 'http://localhost:3000';
const backendOrigin = 'http://127.0.0.1:8000';
const path = `/workspaces/${workspaceId}/tasks/${taskId}/file-edit-proposals/${proposalId}`;

function detail() {
    return {
        workspace_id: workspaceId, task_id: taskId, proposal_id: proposalId,
        status: 'pending', relative_path: 'src/中文.txt', baseline_sha256: 'a'.repeat(64),
        proposed_sha256: 'b'.repeat(64), diff: '-old\n+new\n', diff_truncated: false,
        created_at: '2026-09-21T12:00:00.123456+00:00',
    };
}

function request(init: RequestInit = {}) {
    return new Request(`${browserOrigin}/api${path}`, init);
}

function route(req = request(), workspace = workspaceId, task = taskId, proposal = proposalId) {
    return GET(req, { params: Promise.resolve({ workspaceId: workspace, taskId: task, proposalId: proposal }) });
}

async function assertFailure(response: Response, status: number) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await response.json(), { message: '文件修改提案详情暂时不可用' });
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


test('exact URL, trusted credentials and public projection', async (t) => {
    const mock = t.mock.method(globalThis, 'fetch', async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
        assert.equal(String(url), `${backendOrigin}${path}`);
        assert.equal(init?.method, 'GET');
        assert.equal(init?.cache, 'no-store');
        assert.equal(init?.redirect, 'error');
        assert.equal(init?.body, undefined);
        assert.deepEqual([...new Headers(init?.headers)], [['x-local-runtime-token', token]]);
        return Response.json({ ...detail(), bound_root: 'PRIVATE', proposed_content: 'PRIVATE', user_id: 1 }, {
            headers: { 'Set-Cookie': 'PRIVATE', 'X-Internal': 'PRIVATE' },
        });
    });
    const response = await route(request({ headers: { Cookie: 'PRIVATE', Authorization: 'PRIVATE', 'X-Local-Runtime-Token': 'forged' } }));
    assert.equal(runtime, 'nodejs');
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('x-internal'), null);
    assert.deepEqual(await response.json(), detail());
    assert.equal(mock.mock.callCount(), 1);
});

for (const field of ['workspace', 'task', 'proposal']) {
    for (const invalid of ['bad', 'A'.repeat(32), 'a'.repeat(32) + '\n']) {
        test(`reject ${field} ${JSON.stringify(invalid)}`, async (t) => {
            const mock = t.mock.method(globalThis, 'fetch');
            await assertFailure(await route(request(), field === 'workspace' ? invalid : workspaceId,
                field === 'task' ? invalid : taskId, field === 'proposal' ? invalid : proposalId), 422);
            assert.equal(mock.mock.callCount(), 0);
        });
    }
}
for (const query of ['user_id=1', 'before=1', 'proposal_id=x', 'x=1&x=2', 'x=']) {
    test(`query ${query} rejected before fetch`, async (t) => {
        const mock = t.mock.method(globalThis, 'fetch');
        await assertFailure(await route(new Request(`${browserOrigin}/api${path}?${query}`)), 422);
        assert.equal(mock.mock.callCount(), 0);
    });
}
for (const [field, value] of [
    ['workspace_id', taskId], ['task_id', workspaceId], ['proposal_id', workspaceId],
    ['status', 'approved'], ['relative_path', ''], ['baseline_sha256', 'x'], ['proposed_sha256', 'A'.repeat(64)],
    ['created_at', '2026-02-30T00:00:00Z'], ['diff', ''], ['diff', null],
    ['diff', '😀'.repeat(16385)], ['diff_truncated', 'false'],
] as const) {
    test(`invalid upstream field ${field} is safe 502`, async (t) => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ ...detail(), [field]: value }));
        await assertFailure(await route(), 502);
    });
}
test('parser rejects missing fields and nonobjects', () => {
    for (const raw of [null, [], true, 'data', 42]) assert.equal(readFileEditProposalDetail(raw, workspaceId, taskId, proposalId), null);
    for (const field of Object.keys(detail())) {
        assert.equal(readFileEditProposalDetail({ ...detail(), [field]: undefined }, workspaceId, taskId, proposalId), null);
    }
});
test('Unicode boundary and truncated diff preserved without normalization', () => {
    const payload = { ...detail(), diff: '😀'.repeat(16384), relative_path: '😀'.repeat(4096), diff_truncated: true };
    assert.deepEqual(readFileEditProposalDetail(payload, workspaceId, taskId, proposalId), payload);
    assert.equal(readFileEditProposalDetail(payload, 'bad', taskId, proposalId), null);
    assert.equal(readFileEditProposalDetail({ ...payload, relative_path: 'a'.repeat(4097) }, workspaceId, taskId, proposalId), null);
});
test('unused error response body is cancelled', async (t) => {
    let cancelled = false;
    t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
        cancel() { cancelled = true; },
    }), { status: 404 }));
    await assertFailure(await route(), 404);
    assert.equal(cancelled, true);
});
