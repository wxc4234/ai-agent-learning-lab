import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const fixture = await mkdtemp('/private/tmp/agent-preview-');
const root = `${await realpath(fixture)}/project`;
await mkdir(root);
const sources = {
    success: '\ufeffcount = old\r\n',
    delete: 'old',
    ambiguous: 'old old\n',
    truncated: 'old' + 'X'.repeat(20000),
};
for (const [name, content] of Object.entries(sources)) await writeFile(`${root}/${name}.txt`, content);
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let posts = 0;
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
});
async function setup(name, bound) {
    const response = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(response.status(), 201);
    const workspace = await response.json();
    if (bound) {
        const binding = await page.request.put(`${base}/api/workspaces/${workspace.external_id}/directory`, {
            headers: { Origin: base }, data: { root_path: root },
        });
        assert.equal(binding.status(), 200);
    }
    const created = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, {
        headers: { Origin: base }, data: { title: name },
    });
    assert.equal(created.status(), 201);
    const task = await created.json();
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').waitFor();
    return { workspace, task };
}
async function send(prompt, answer) {
    await page.getByLabel('你的问题').fill(prompt);
    const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
    // 键盘发送，经过真实浏览器→BFF→API，不拦截聊天响应。
    await page.getByLabel('你的问题').press('Enter');
    const response = await pending;
    assert.equal(response.status(), 200);
    await page.getByText(answer, { exact: true }).waitFor();
    const runId = response.headers()['x-run-id'];
    // Chrome 对已消费的流式正文不保证可再次读取；页面验证展示，GET 验证持久化事件。
    let events = [];
    for (let attempt = 0; attempt < 30; attempt++) {
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const detail = await stored.json();
        events = detail.events.map(event => ({ ...event.payload, type: event.event_type }));
        if (events.some(event => event.type === 'RUN_FINISHED')) break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.equal(events.filter(event => event.type === 'RUN_FINISHED').length, 1);
    const expand = page.getByRole('button', { name: '展开详情', exact: true });
    if (await expand.isVisible()) await expand.click();
    return { events, runId };
}
try {
    for (const marker of Object.keys(sources)) {
        const answer = marker === 'ambiguous' ? '预览失败：待替换文本存在多个匹配，请提供更完整的上下文'
            : `仅生成预览，尚未写入${marker === 'truncated' ? '，Diff不完整' : ''}`;
        const { workspace, task } = await setup(`修改预览-${marker}`, true);
        const { events, runId } = await send(`[preview-${marker}] 生成修改预览`, answer);
        assert.deepEqual(events.filter(e => e.type === 'TOOL_CALL_START').map(e => e.tool_name), ['preview_file_edit']);
        const results = events.filter(e => e.type === 'TOOL_CALL_RESULT');
        const failures = events.filter(e => e.type === 'TOOL_CALL_ERROR');
        if (marker === 'ambiguous') {
            assert.equal(results.length, 0);
            assert.equal(failures.length, 1);
            assert.equal(failures[0].details, 'edit_target_ambiguous');
        } else {
            assert.equal(failures.length, 0);
            assert.equal(results.length, 1);
            const result = JSON.parse(results[0].result);
            assert.deepEqual(Object.keys(result).sort(), ['status', 'relative_path', 'baseline_sha256', 'before_byte_count', 'after_byte_count', 'diff', 'diff_truncated'].sort());
            assert.equal(result.status, 'preview_only');
            assert.equal(result.relative_path, `${marker}.txt`);
            assert.equal(result.baseline_sha256, createHash('sha256').update(sources[marker]).digest('hex'));
            assert.equal(result.before_byte_count, Buffer.byteLength(sources[marker]));
            assert.equal(result.after_byte_count, Buffer.byteLength(sources[marker].replace('old', marker === 'delete' ? '' : 'new')));
            assert.equal(result.diff_truncated, marker === 'truncated');
            assert.ok(result.diff.startsWith('--- before\n+++ after\n'));
            assert.ok(result.diff.includes('old'));
            if (marker === 'success') assert.ok(result.diff.includes('new') && result.diff.includes('\\r\\n'));
            if (marker === 'truncated') assert.equal(result.diff.length, 16384);
        }
        await page.getByText('preview_file_edit', { exact: true }).waitFor();
        if (marker !== 'ambiguous') {
            const result = JSON.parse(results[0].result);
            await page.getByText('仅预览，未写入', { exact: true }).waitFor();
            await page.getByText('调用完成', { exact: true }).waitFor();
            await page.getByText(result.diff_truncated ? 'Diff 已截断，审阅内容不完整' : 'Diff 未截断', { exact: true }).waitFor();
            const shown = page.getByLabel('修改 Diff', { exact: true });
            assert.equal(await shown.textContent(), result.diff);
            assert.ok(await shown.evaluate(el => el.clientHeight <= 256));
            await shown.focus();
            assert.ok(await shown.evaluate(el => el === document.activeElement));
            if (result.diff_truncated) assert.ok(await shown.evaluate(el => el.scrollHeight > el.clientHeight));
            const summary = page.locator('summary').filter({ hasText: '原文基线 SHA-256' });
            const details = summary.locator('..');
            assert.equal(await details.getAttribute('open'), null);
            await summary.focus();
            await summary.press('Enter');
            assert.equal(await details.locator('code').textContent(), result.baseline_sha256);
            assert.ok(await details.locator('code').isVisible());
            await summary.press('Enter');
            if (marker === 'delete') await page.getByText('0 字节', { exact: true }).waitFor();
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/preview-${marker}-${width}.png` });
        }
        assert.ok(!(await page.locator('body').innerText()).includes(root));
        assert.ok(!JSON.stringify(events).includes(root));
        assert.ok(!JSON.stringify(events).includes('updated_content'));
        const before = posts;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        assert.equal(posts, before);
        const history = await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/messages`);
        assert.equal(history.status(), 200);
        assert.ok((await history.text()).includes(answer));
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const run = await stored.json();
        assert.equal(run.status, 'done');
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_RESULT').length, results.length);
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_ERROR').length, failures.length);
        for (const [name, content] of Object.entries(sources)) {
            assert.deepEqual(await readFile(`${root}/${name}.txt`), Buffer.from(content));
        }
        console.log(`PASS ${marker}: preview-only/real file unchanged/hash/PC/persistence/no replay`);
    }
    assert.deepEqual(errors, []);
} catch (error) {
    await page.screenshot({ path: `${output}/preview-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
    console.log('Temporary preview fixture removed.');
}
