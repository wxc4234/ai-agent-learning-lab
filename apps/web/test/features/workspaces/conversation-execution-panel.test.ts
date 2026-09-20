import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { readConversationExecutionStatus } from '../../../src/features/workbench/conversation-execution-data.ts';

// 执行组件里的真实 refresh 函数，不复制异步状态逻辑；挂载/切换由浏览器补验。
const source = ts.createSourceFile('panel.tsx', readFileSync(new URL(
    '../../../src/features/workbench/components/conversation-execution-panel.tsx', import.meta.url,
), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let refresh: ts.FunctionDeclaration | undefined;
function visit(node: ts.Node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'refresh') refresh = node;
    ts.forEachChild(node, visit);
}
visit(source);
assert.ok(refresh);
const code = ts.transpileModule(refresh.getText(source), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const sessionId = 'a'.repeat(32);
const idle = { session_id: sessionId, occupied: false, acquired_at: null };
function harness(fetcher: typeof fetch) {
    const states: Array<{ phase: string; status?: unknown }> = [];
    const controllerRef = { current: null as AbortController | null };
    const run: () => Promise<void> = new Function(
        'sessionId', 'controllerRef', 'setState', 'fetch', 'readConversationExecutionStatus',
        `${code}; return refresh;`,
    )(sessionId, controllerRef, (state: { phase: string }) => states.push(state), fetcher, readConversationExecutionStatus);
    return { states, controllerRef, run };
}

test('duplicate click starts only one request and success clears request ref', async () => {
    let release!: (response: Response) => void;
    let calls = 0;
    const h = harness(async () => {
        calls++;
        return new Promise<Response>(resolve => { release = resolve; });
    });
    const pending = h.run();
    await h.run();
    assert.equal(calls, 1);
    assert.deepEqual(h.states, [{ phase: 'loading' }]);
    release(Response.json(idle));
    await pending;
    assert.equal(h.states.at(-1)?.phase, 'ready');
    assert.deepEqual(h.states.at(-1)?.status, idle);
    assert.equal(h.controllerRef.current, null);
});

for (const mode of ['network', 'json', 'contract', 'http']) {
    test(`${mode} error clears ref and allows explicit retry`, async () => {
        let calls = 0;
        const h = harness(async () => {
            if (++calls > 1) return Response.json(idle);
            if (mode === 'network') throw new Error('PRIVATE');
            if (mode === 'json') return new Response('bad JSON');
            if (mode === 'http') return Response.json(idle, { status: 502 });
            return Response.json({ ...idle, session_id: 'wrong' });
        });
        await h.run();
        assert.equal(h.states.at(-1)?.phase, 'error');
        assert.equal(h.controllerRef.current, null);
        assert.equal(calls, 1);
        await h.run();
        assert.equal(h.states.at(-1)?.phase, 'ready');
    });
}

for (const phase of ['fetch', 'body']) {
    test(`timeout during ${phase} shows unknown rather than remaining loading`, async (t) => {
        const deadline = new AbortController();
        t.mock.method(AbortSignal, 'timeout', (ms: number) => {
            assert.equal(ms, 25_000);
            return deadline.signal;
        });
        const h = harness(async (_url, init) => {
            assert.ok(init?.signal);
            assert.equal(init.cache, 'no-store');
            const response = Response.json(idle);
            const cancel = async () => { deadline.abort(); return idle; };
            if (phase === 'fetch') deadline.abort();
            else t.mock.method(response, 'json', cancel);
            return response;
        });
        await h.run();
        assert.equal(h.states.at(-1)?.phase, 'error');
        assert.equal(h.controllerRef.current, null);
    });
}

test('unmounted old request cannot write state or clear replacement request', async () => {
    let release!: (response: Response) => void;
    const h = harness(async () => new Promise<Response>(resolve => { release = resolve; }));
    const pending = h.run();
    h.controllerRef.current?.abort();
    const replacement = new AbortController();
    h.controllerRef.current = replacement;
    release(Response.json(idle));
    await pending;
    assert.deepEqual(h.states, [{ phase: 'loading' }]);
    assert.equal(h.controllerRef.current, replacement);
});
