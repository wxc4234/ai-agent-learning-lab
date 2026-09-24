import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadLatestRunSummary } from '../../../src/features/workbench/latest-run-summary.ts';
import type { TaskRunPage } from '../../../src/features/workbench/task-run-data.ts';
import type { RunDetail } from '../../../src/features/workbench/run-detail-data.ts';
const task = { workspace_id: 'a'.repeat(32), external_id: 'b'.repeat(32), conversation_id: 'session-a' };
const item = { run_id: 7, status: 'done', started_at: '2026-09-24T00:00:00Z', finished_at: '2026-09-24T00:00:01Z', duration_ms: 1000 };
const metrics = { model_usage: null, model_duration_ms: 300, tool_duration_ms: 0, estimated_cost_cny: null, pricing: { model: "test", tier: "peak", cache_hit_input_cny_per_million: "0.1", cache_miss_input_cny_per_million: "1", output_cny_per_million: "1" } };
function fixtures(): [TaskRunPage, RunDetail] {
    return [
        { workspace_id: task.workspace_id, task_id: task.external_id, items: [item], next_cursor: null },
        { ...item, events: [
            { id: 1, event_type: 'RUN_STARTED', created_at: item.started_at, payload: { session_id: task.conversation_id, prompt_length: 1 } },
            { id: 2, event_type: 'RUN_FINISHED', created_at: item.finished_at, payload: { steps_taken: 1, metrics } },
        ] },
    ];
}
test('restores saved summary using task-scoped latest run', async t => {
    const responses = fixtures();
    const calls: string[] = [];
    t.mock.method(globalThis, 'fetch', async (url: string, options: RequestInit) => {
        calls.push(url); assert.equal(options.cache, 'no-store');
        return Response.json(responses.shift());
    });
    assert.deepEqual(await loadLatestRunSummary(task, new AbortController().signal), { status: 'done', summary: { stepsTaken: 1, metrics } });
    assert.deepEqual(calls, [`/api/workspaces/${task.workspace_id}/tasks/${task.external_id}/runs?limit=1`, '/api/runs/7']);
});
for (const mode of ['foreign-task', 'foreign-session', 'network', 'empty', 'failed-without-metrics', 'cancelled']) {
    test(mode, async t => {
        const [page, detail] = fixtures();
        if (mode === 'foreign-task') page.task_id = 'c'.repeat(32);
        if (mode === 'foreign-session') detail.events![0].payload = { session_id: 'other', prompt_length: 1 };
        if (mode === 'empty') page.items = [];
        if (mode === 'failed-without-metrics') {
            detail.status = 'error';
            detail.events![1] = { id: 2, event_type: 'RUN_ERROR', created_at: item.finished_at,
                payload: { code: 'invalid_model_decision', message: '失败', reason: 'empty_response' } };
        }
        const controller = new AbortController();
        if (mode === 'cancelled') controller.abort();
        const responses = [page, detail];
        const fetch = t.mock.method(globalThis, 'fetch', async () => {
            if (mode === 'network') throw new Error('unavailable');
            return Response.json(responses.shift());
        });
        if (['empty', 'failed-without-metrics'].includes(mode)) assert.equal(await loadLatestRunSummary(task, controller.signal), null);
        else await assert.rejects(loadLatestRunSummary(task, controller.signal));
        if (mode === 'empty') assert.equal(fetch.mock.callCount(), 1);
        if (mode === 'cancelled') assert.equal(fetch.mock.callCount(), 0);
    });
}
