import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseFileEditPreview } from '../../../src/features/chat/file-edit-preview-view.ts';
import { renderToolResult, renderHistoricalEvent } from './render-tool-result.ts';

const payload = {
    status: 'preview_only', relative_path: 'src/file.txt', baseline_sha256: 'a'.repeat(64),
    before_byte_count: 4, after_byte_count: 0, diff: '--- before\n+++ after\n@@ -1 +0 @@\n-"old\\n"\n', diff_truncated: false,
};
for (const truncated of [true, false]) {
    test(`patch preview card truncated=${truncated}`, () => {
        const raw = JSON.stringify({ ...payload, diff_truncated: truncated, updated_content: 'PRIVATE' });
        const view = parseFileEditPreview('preview_file_patch', raw);
        assert.ok(view);
        assert.ok(!JSON.stringify(view).includes('PRIVATE'));
        const html = renderToolResult('preview_file_patch', raw);
        for (const text of ['仅预览，未写入', '0 字节', 'SHA-256', 'aria-label="修改 Diff"', 'tabindex="0"']) assert.ok(html.includes(text));
        assert.ok(html.includes(truncated ? 'Diff 已截断，审阅内容不完整' : 'Diff 未截断'));
        assert.ok(!html.includes('PRIVATE') && !html.includes('<button'));
    });
}
for (const [name, value] of Object.entries({ status: 'applied', diff: 'x'.repeat(16385), diff_truncated: 'false', baseline_sha256: 'bad', before_byte_count: -1, after_byte_count: 262145, relative_path: '' })) {
    test(`patch invalid ${name} falls back`, () => {
        const raw = JSON.stringify({ ...payload, [name]: value });
        assert.equal(parseFileEditPreview('preview_file_patch', raw), null);
        const html = renderToolResult('preview_file_patch', raw);
        assert.ok(html.includes('预览结果格式未识别'));
        assert.ok(!html.includes('aria-label="文件修改预览"'));
    });
}
test('patch content escaped and unrelated tool cannot claim preview', () => {
    const raw = JSON.stringify({ ...payload, diff: '<script>alert(1)</script>' });
    const html = renderToolResult('preview_file_patch', raw);
    assert.ok(html.includes('&lt;script&gt;') && !html.includes('<script>'));
    assert.equal(parseFileEditPreview('unknown', raw), null);
    assert.ok(renderToolResult('preview_file_patch', 'not json').includes('预览结果格式未识别'));
});
for (const tool of ['preview_file_patch', 'preview_file_edit']) {
    test(`historical ${tool} result reuses readonly card`, () => {
        const html = renderHistoricalEvent({ event_type: 'TOOL_CALL_RESULT', payload: { tool_name: tool, result: JSON.stringify(payload) } }, { workspaceId: 'w', taskId: 't' });
        assert.ok(html.includes('仅预览，未写入'));
        assert.ok(!html.includes('<button'));
    });

}
