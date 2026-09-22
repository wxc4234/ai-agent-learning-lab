import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderHistoricalEvent } from './render-tool-result.ts';

const scope = { workspaceId: 'a'.repeat(32), taskId: 'b'.repeat(32) };
const receipt = JSON.stringify({ proposal_id: 'c'.repeat(32), status: 'pending',
    relative_path: '<script>file.txt</script>', baseline_sha256: 'a'.repeat(64),
    proposed_sha256: 'b'.repeat(64), created_at: '2026-09-21T00:00:00Z', diff_truncated: false });
function event(type = 'TOOL_CALL_RESULT', tool = 'create_file_edit_proposal', result: unknown = receipt) {
    return { event_type: type, payload: { tool_name: tool, result } };
}

test('historical success shows creation receipt and idle detail without auto fetching', (t) => {
    const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('unexpected fetch'); });
    const html = renderHistoricalEvent(event(), scope);
    assert.ok(html.includes('提案已保存，待审批'));
    assert.ok(html.includes('查看提案详情'));
    assert.ok(!html.includes('批准提案'));
    assert.ok(!html.includes('<script>'));
    assert.equal(fetch.mock.callCount(), 0);
});
for (const field of ['workspaceId', 'taskId']) {
    test(`history uses current ${field}, never a scope from receipt`, () => {
        const forged = JSON.stringify({ ...JSON.parse(receipt), workspace_id: scope.workspaceId, task_id: scope.taskId });
        const html = renderHistoricalEvent(event('TOOL_CALL_RESULT', 'create_file_edit_proposal', forged), { ...scope, [field]: 'invalid' });
        assert.ok(html.includes('当前任务信息不完整'));
        assert.ok(!html.includes('查看提案详情'));
    });
}
for (const [name, value] of [
    ['error', event('TOOL_CALL_ERROR')],
    ['unknown event', event('FUTURE_EVENT')],
    ['other tool', event('TOOL_CALL_RESULT', 'preview_file_edit')],
    ['invalid receipt', event('TOOL_CALL_RESULT', 'create_file_edit_proposal', '{"status":"approved"}')],
    ['non-text result', event('TOOL_CALL_RESULT', 'create_file_edit_proposal', { proposal_id: 'c'.repeat(32) })],
] as const) {
    test(`${name} cannot expose approval entry`, () => {
        assert.ok(!renderHistoricalEvent(value, scope).includes('查看提案详情'));
    });
}
