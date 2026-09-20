import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readHistoricalRunSummary } from '../../../src/features/workbench/historical-run-summary.ts';
import { readRunDetail, type RunDetail } from '../../../src/features/workbench/run-detail-data.ts';
import { createRunSummaryMetrics } from '../../../src/features/chat/run-summary-view.ts';

const metrics = {
    model_usage: null, model_duration_ms: null, tool_duration_ms: 0,
    estimated_cost_cny: '0.00128420',
    pricing: { model: 'test', tier: 'peak', cache_hit_input_cny_per_million: '0.10',
        cache_miss_input_cny_per_million: '3.0', output_cny_per_million: '9.0' },
};
function detail(status = 'done'): RunDetail {
    return { run_id: 1, status, started_at: '2026-09-20T00:00:00Z', finished_at: '2026-09-20T00:00:01Z', duration_ms: 1000,
        events: [{ id: 1, event_type: status === 'done' ? 'RUN_FINISHED' : 'RUN_ERROR', created_at: '2026-09-20T00:00:01Z',
            payload: { steps_taken: 2, metrics, ...(status === 'error' ? { code: 'max_steps_exceeded', message: '失败' } : {}) } }] };
}
for (const status of ['done', 'error'] as const) {
    test(`${status} restores validated payload through detail parser`, () => {
        const parsed = readRunDetail(detail(status), '1');
        assert.ok(parsed);
        const result = readHistoricalRunSummary(parsed);
        assert.deepEqual(result, { status, summary: { stepsTaken: 2, metrics } });
        const fields = createRunSummaryMetrics(result!.summary);
        assert.equal(fields.find(x => x.key === 'total_tokens')?.value, '暂无数据');
        assert.equal(fields.find(x => x.key === 'model_duration')?.value, '暂无数据');
        assert.equal(fields.find(x => x.key === 'tool_duration')?.value, '0 ms');
        assert.equal(fields.find(x => x.key === 'estimated_cost')?.value, '¥0.00128420');
    });
}
for (const status of ['running', 'aborted', 'future', 'finished']) {
    test(`does not invent summary for ${status}`, () => {
        assert.equal(readHistoricalRunSummary({ ...detail(), status }), null);
    });
}
for (const mutation of ['unfinished', 'missing', 'duplicate', 'conflict', 'wrong-terminal']) {
    test(`rejects ${mutation}`, () => {
        const value = detail();
        if (mutation === 'unfinished') value.finished_at = null;
        if (mutation === 'missing') value.events = [];
        if (mutation === 'duplicate') value.events.push({ ...value.events[0], id: 2 });
        if (mutation === 'conflict') value.events.push({ ...value.events[0], id: 2, event_type: 'RUN_ABORTED', payload: { reason: 'user' } });
        if (mutation === 'wrong-terminal') value.status = 'error';
        assert.equal(readHistoricalRunSummary(value), null);
    });
}
for (const payload of [
    { code: 'error', message: 'failure' }, { reason: 'timeout' },
    { code: 'error', message: 'failure', steps_taken: 2 },
    { code: 'error', message: 'failure', metrics },
]) {
    test(`missing/partial error summary ${JSON.stringify(payload)}`, () => {
        const value = detail('error');
        value.events[0].payload = payload;
        assert.equal(readHistoricalRunSummary(value), null);
    });
}
for (const patch of [
    { steps_taken: -1 }, { steps_taken: 1.5 }, { metrics: null },
    { metrics: { ...metrics, tool_duration_ms: -1 } },
    { metrics: { ...metrics, estimated_cost_cny: 0.1 } },
    { metrics: { ...metrics, estimated_cost_cny: '1e-3' } },
]) {
    test(`invalid metrics ${JSON.stringify(patch)}`, () => {
        const value = detail();
        value.events[0].payload = { ...value.events[0].payload, ...patch };
        assert.equal(readHistoricalRunSummary(value), null);
    });
}
test('envelope type wins and unknown events do not supply fields', () => {
    const value = detail();
    value.events[0].payload.type = 'RUN_ERROR';
    value.events[0].payload.secret = 'PRIVATE';
    value.events.push({ id: 2, event_type: 'FUTURE', created_at: value.finished_at!, payload: { steps_taken: 999 } });
    assert.deepEqual(readHistoricalRunSummary(value), { status: 'done', summary: { stepsTaken: 2, metrics } });
});
test('unknown cost stays null and actual zero steps stays zero', () => {
    const value = detail();
    value.events[0].payload = { steps_taken: 0, metrics: { ...metrics, estimated_cost_cny: null } };
    const result = readHistoricalRunSummary(value);
    assert.ok(result);
    const fields = createRunSummaryMetrics(result.summary);
    assert.equal(fields.find(x => x.key === 'steps')?.value, '0 步');
    assert.equal(fields.find(x => x.key === 'estimated_cost')?.value, '暂无数据');
});
