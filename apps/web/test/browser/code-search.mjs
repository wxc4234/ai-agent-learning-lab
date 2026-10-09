import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/code-search/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(45000);
const errors = [], report = [], directRequests = [];
let posts = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
    if (new URL(request.url()).port === '18000') directRequests.push(request.url());
});
const answers = {
    match: '取消函数返回停止状态。来源：src/取消.py:1。这是本次检索的代码快照。',
    empty: '最近检索窗口没有兼容的代码快照，不能据此判断整个项目没有相关代码。',
    error: '本次代码检索失败，不能据此判断没有相关代码。',
};

async function poll(read, accept) {
    const deadline = Date.now() + 15000;
    do {
        const value = await read();
        if (accept(value)) return value;
        await delay(100);
    } while (Date.now() < deadline);
    throw new Error('Timed out waiting for real server evidence');
}

async function readRun(id) {
    const response = await page.request.get(`${base}/api/runs/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
}

async function verifySimpleUI() {
    assert.equal(await page.getByRole('button', { name: '高级详情', exact: true }).count(), 0);
    assert.equal(await page.getByRole('region', { name: '代码查询预览', exact: true }).count(), 0);
    assert.equal(await page.locator('#workbench-details').isVisible(), false);
    const body = await page.locator('body').innerText();
    for (const internal of ['PRIVATE_PROVIDER_DETAIL', 'EMBED_PRIVATE', 'space_id', 'batch_id', 'fixture-model']) {
        assert.ok(!body.includes(internal), `private field leaked: ${internal}`);
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
}

try {
    for (const fixture of fixtures) {
        const { mode, workspace_id, task_id } = fixture;
        await page.setViewportSize({ width: 1366, height: 900 });
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        const input = page.getByLabel('你的问题', { exact: true });
        await input.waitFor();
        await page.getByText('检索代码或笔记时，查询和命中片段会发送给你配置的相应模型。', { exact: true }).waitFor();
        assert.equal(await input.getAttribute('aria-describedby'), 'retrieval-model-notice');
        await input.fill(`[${mode}] 查找取消函数并引用来源`);
        const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
        await input.press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        const runId = response.headers()['x-run-id'];
        assert.ok(runId);
        if (mode === 'cancel') {
            // 等待受控供应商确实开始读流；避免只取消了尚未开始的请求。
            await poll(() => readFile(`${output}/cancel-started.json`, 'utf8').catch(() => null), Boolean);
            const cancelling = page.waitForResponse(r => r.url().endsWith(`/api/runs/${runId}/cancel`));
            await page.getByRole('button', { name: '停止生成', exact: true }).click();
            assert.equal((await cancelling).status(), 204);
        }
        const run = await poll(() => readRun(runId), value => ['done', 'aborted', 'error'].includes(value.status));
        assert.equal(run.status, mode === 'cancel' ? 'aborted' : 'done', JSON.stringify(run));
        if (mode !== 'cancel') await page.getByText(answers[mode], { exact: true }).waitFor();
        const starts = run.events.filter(e => e.event_type === 'TOOL_CALL_START');
        const results = run.events.filter(e => e.event_type === 'TOOL_CALL_RESULT');
        const failures = run.events.filter(e => e.event_type === 'TOOL_CALL_ERROR');
        assert.equal(starts.length, 1);
        assert.equal(starts[0].payload.tool_name, 'search_code');
        assert.equal(results.length, ['match', 'empty'].includes(mode) ? 1 : 0);
        assert.equal(failures.length, mode === 'error' ? 1 : 0);
        if (mode === 'match') {
            const result = JSON.parse(results[0].payload.result);
            assert.equal(result.matches[0].relative_path, 'src/取消.py');
            assert.equal(result.matches[0].start_line, 1);
            await page.locator('code').filter({ hasText: 'src/取消.py:1' }).waitFor();
        } else if (mode === 'empty') {
            assert.equal(JSON.parse(results[0].payload.result).status, 'not_found_in_window');
        } else if (mode === 'error') {
            assert.equal(failures[0].payload.details, 'code_search_unavailable');
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await verifySimpleUI();
            await page.screenshot({ path: `${output}/${mode}-${width}.png` });
        }
        const count = posts;
        await page.reload();
        await page.getByLabel('你的问题', { exact: true }).waitFor();
        // 等待历史请求完成；刷新绝不能启动新的聊天或检索。
        await page.waitForFunction(() => !document.querySelector('#prompt')?.disabled);
        if (mode !== 'cancel') await page.getByText(answers[mode], { exact: true }).waitFor();
        await verifySimpleUI();
        assert.equal(posts, count);
        const after = await readRun(runId);
        assert.deepEqual(after.events, run.events);
        const history = await page.request.get(`${base}/api/workspaces/${workspace_id}/tasks/${task_id}/messages`);
        assert.equal(history.status(), 200);
        const raw = await history.text();
        if (mode === 'cancel') assert.ok(!raw.includes('[cancel]'));
        else assert.ok(raw.includes(mode === 'match' ? 'src/取消.py:1' : answers[mode]));
        await page.screenshot({ path: `${output}/${mode}-restored.png` });
        report.push({ mode, run_id: Number(runId), status: run.status, history_did_not_replay: true });
        console.log(`PASS ${mode}: visible result, desktop layouts, persisted history without replay`);
    }
    assert.equal(posts, fixtures.length);
    assert.deepEqual(errors, []);
    assert.deepEqual(directRequests, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(report, null, 4));
    console.log('PASS code search PC: 4 scenarios, 1366/1920, readable citation, cancellation, refresh without replay.');
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png` });
    console.error((await page.locator('body').innerText()).slice(-4000));
    throw error;
} finally {
    await browser.close();
}
