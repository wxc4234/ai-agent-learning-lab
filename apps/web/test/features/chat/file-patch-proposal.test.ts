import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseFileEditProposal } from '../../../src/features/chat/file-edit-proposal-view.ts';
import { renderToolResult, renderHistoricalEvent } from './render-tool-result.ts';

const payload = {
    status: 'pending', proposal_id: 'a'.repeat(32), relative_path: 'src/file.txt',
    baseline_sha256: 'b'.repeat(64), proposed_sha256: 'c'.repeat(64),
    created_at: '2026-09-24T12:00:00+08:00', diff_truncated: false,
};
for (const truncated of [true, false]) {
    test(`patch proposal receipt truncated=${truncated}`, () => {
        const raw = JSON.stringify({ ...payload, diff_truncated: truncated, proposed_content: 'PRIVATE', bound_root: '/PRIVATE' });
        const view = parseFileEditProposal('create_file_patch_proposal', raw);
        assert.ok(view);
        assert.ok(!JSON.stringify(view).includes('PRIVATE'));
        const html = renderToolResult('create_file_patch_proposal', raw);
        for (const text of ['提案已保存，待审批', '尚未写入文件', 'SHA-256']) assert.ok(html.includes(text));
        assert.ok(html.includes(truncated ? 'Diff 已截断，审阅内容不完整' : '保存的 Diff 未截断'));
        assert.ok(!html.includes('PRIVATE'));
    });
}
for (const [field, value] of Object.entries({ status: 'approved', proposal_id: 'bad', baseline_sha256: 'bad', proposed_sha256: 'bad', created_at: '2026-02-30T00:00:00Z', relative_path: '', diff_truncated: 'false' })) {
    test(`invalid patch receipt ${field} falls back`, () => {
        const raw = JSON.stringify({ ...payload, [field]: value });
        assert.equal(parseFileEditProposal('create_file_patch_proposal', raw), null);
        const html = renderToolResult('create_file_patch_proposal', raw);
        assert.ok(html.includes('提案回执格式未识别'));
        assert.ok(!html.includes('aria-label="文件修改提案回执"'));
    });
}
for (const tool of ['create_file_patch_proposal', 'create_file_edit_proposal']) {
    test(`historical ${tool} uses scoped proposal details`, () => {
        const html = renderHistoricalEvent({ event_type: 'TOOL_CALL_RESULT', payload: { tool_name: tool, result: JSON.stringify(payload) } }, { workspaceId: 'a'.repeat(32), taskId: 'b'.repeat(32) });
        assert.ok(html.includes('文件修改提案回执') && html.includes('查看提案详情'));
    });
}
test('error cannot become a pending receipt and path is escaped', () => {
    const html = renderHistoricalEvent({ event_type: 'TOOL_CALL_ERROR', payload: { tool_name: 'create_file_patch_proposal', message: '提案保存结果未确认' } }, { workspaceId: 'a'.repeat(32), taskId: 'b'.repeat(32) });
    assert.ok(!html.includes('文件修改提案回执'));
    assert.ok(renderToolResult('create_file_patch_proposal', JSON.stringify({ ...payload, relative_path: '<script>x</script>' })).includes('&lt;script&gt;'));
    assert.equal(parseFileEditProposal('preview_file_patch', JSON.stringify(payload)), null);
});
