import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-proposal/output/playwright';
await mkdir(output, { recursive: true });
const fixture = await mkdtemp('/private/tmp/agent-proposal-');
const root = `${await realpath(fixture)}/project`;
await mkdir(root);
const sources = {
    success: '\ufeffcount = old\r\n',
    delete: 'old',
    preview: 'old',
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
let detailGets = 0;
page.on('request', request => {
    if (request.method() === 'GET' && request.url().includes('/file-edit-proposals/')) detailGets++;
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
const evidence = [];
try {
    for (const marker of Object.keys(sources)) {
        const answer = marker === 'ambiguous' ? '提案失败：待替换文本存在多个匹配，请提供更完整的上下文'
            : marker === 'preview' ? '仅生成预览，尚未写入' : '提案已保存，等待审批，文件尚未修改';
        const { workspace, task } = await setup(`修改提案-${marker}`, true);
        const { events, runId } = await send(`[proposal-${marker}] 创建待审批提案；preview场景仅预览`, answer);
        assert.deepEqual(events.filter(e => e.type === 'TOOL_CALL_START').map(e => e.tool_name), [marker === 'preview' ? 'preview_file_edit' : 'create_file_edit_proposal']);
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
            if (marker === 'preview') {
                assert.equal(result.status, 'preview_only');
            } else {
                assert.deepEqual(Object.keys(result).sort(), ['proposal_id', 'status', 'relative_path', 'baseline_sha256', 'proposed_sha256', 'diff_truncated', 'created_at'].sort());
                assert.equal(result.status, 'pending');
                assert.match(result.proposal_id, /^[0-9a-f]{32}$/);
                assert.ok(Number.isFinite(Date.parse(result.created_at)));
                assert.equal(result.proposed_sha256, createHash('sha256').update(sources[marker].replace('old', marker === 'delete' ? '' : 'new')).digest('hex'));
                // 通用工具详情必须展示真实回执，不依赖受控模型回答代替UI证据。
                await page.getByText(result.proposal_id, { exact: false }).waitFor();
                const card = page.getByRole('region', { name: '文件修改提案回执', exact: true });
                await card.getByText('提案已保存，待审批', { exact: true }).waitFor();
                await card.getByText('尚未写入文件', { exact: true }).waitFor();
                assert.equal(await card.locator('time').getAttribute('datetime'), result.created_at);
                assert.equal(await card.locator('time').textContent(), result.created_at);
                await card.getByText(result.diff_truncated ? 'Diff 已截断，审阅内容不完整' : '保存的 Diff 未截断', { exact: true }).waitFor();
                const summary = card.locator('summary');
                const details = card.locator('details');
                assert.equal(await details.getAttribute('open'), null);
                await summary.focus();
                await summary.press('Enter');
                assert.deepEqual(await details.locator('code').allTextContents(), [result.baseline_sha256, result.proposed_sha256]);
                assert.ok(await details.locator('code').first().isVisible());
                await summary.press('Enter');
                assert.equal(await details.getAttribute('open'), null);
                assert.equal(await card.getByRole('button').count(), 0);
            }
            assert.equal(result.relative_path, `${marker}.txt`);
            assert.equal(result.baseline_sha256, createHash('sha256').update(sources[marker]).digest('hex'));
            assert.equal(result.diff_truncated, marker === 'truncated');
        }
        await page.getByText(marker === 'preview' ? 'preview_file_edit' : 'create_file_edit_proposal', { exact: true }).waitFor();
        evidence.push({ marker, task_id: task.external_id, result: results.length ? JSON.parse(results[0].result) : null,
            expected_content: sources[marker].replace('old', marker === 'delete' ? '' : 'new') });
        if (!['preview', 'ambiguous'].includes(marker)) {
            const panel = page.getByRole('region', { name: '文件修改提案详情', exact: true });
            const result = JSON.parse(results[0].result);
            const endpoint = `${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/file-edit-proposals/${result.proposal_id}`;
            const beforeDetails = detailGets;
            assert.equal(await panel.getByLabel('提案 Diff', { exact: true }).count(), 0);
            const pendingDetail = page.waitForResponse(r => r.url() === endpoint);
            await panel.getByRole('button', { name: '查看提案详情', exact: true }).press('Enter');
            const detailResponse = await pendingDetail;
            assert.equal(detailResponse.status(), 200);
            assert.equal(detailResponse.headers()['cache-control'], 'no-store');
            const detail = await detailResponse.json();
            const diff = panel.getByLabel('提案 Diff', { exact: true });
            await diff.waitFor();
            assert.equal(await diff.textContent(), detail.diff);
            assert.equal(detail.proposal_id, result.proposal_id);
            assert.equal(detail.workspace_id, workspace.external_id);
            assert.equal(detail.task_id, task.external_id);
            assert.equal(detail.proposed_sha256, result.proposed_sha256);
            assert.equal(detailGets, beforeDetails + 1);
            await diff.focus();
            assert.ok(await diff.evaluate(el => el === document.activeElement && el.clientHeight <= 256));
            if (marker === 'truncated') assert.ok(await diff.evaluate(el => el.scrollHeight > el.clientHeight));
            assert.ok(!JSON.stringify(detail).includes(root));
            assert.ok(!('proposed_content' in detail));

            if (marker === 'success') {
                // 错误仅在此段浏览器响应注入；首次与重试仍走真实BFF/API。
                await panel.getByRole('button', { name: '收起详情', exact: true }).click();
                await page.route(endpoint, route => route.fulfill({ status: 404, body: 'PRIVATE' }), { times: 1 });
                await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
                await panel.getByRole('alert').filter({ hasText: '提案不存在或不可访问' }).waitFor();
                assert.ok(!(await panel.innerText()).includes('PRIVATE'));
                await panel.getByRole('button', { name: '重试读取详情', exact: true }).click();
                await diff.waitFor();
                assert.equal(await diff.textContent(), detail.diff);

                // 暂挂一个旧响应，取消后发出新读取，再释放旧响应检查不会覆盖。
                await panel.getByRole('button', { name: '收起详情', exact: true }).click();
                let release;
                let intercepted;
                const hold = new Promise(resolve => { release = resolve; });
                const started = new Promise(resolve => { intercepted = resolve; });
                await page.route(endpoint, async route => {
                    intercepted();
                    await hold;
                    await route.fulfill({ json: { ...detail, diff: 'STALE_RESPONSE' } }).catch(() => {});
                }, { times: 1 });
                await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
                await started;
                await panel.getByRole('button', { name: '取消读取', exact: true }).click();
                await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
                await diff.waitFor();
                release();
                assert.equal(await diff.textContent(), detail.diff);
            }
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/proposal-${marker}-${width}.png` });
        }
        assert.ok(!(await page.locator('body').innerText()).includes(root));
        assert.ok(!JSON.stringify(events).includes(root));
        assert.ok(!JSON.stringify(events).includes('updated_content'));
        const before = posts;
        const beforeReloadDetails = detailGets;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        assert.equal(posts, before);
        assert.equal(detailGets, beforeReloadDetails);
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
        console.log(`PASS ${marker}: proposal/real file unchanged/hash/PC/persistence/no replay`);
    }
    assert.deepEqual(errors, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(evidence));
} catch (error) {
    await page.screenshot({ path: `${output}/proposal-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
    console.log('Temporary proposal fixture removed.');
}
