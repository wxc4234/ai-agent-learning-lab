import { openAdvancedDetails } from './workbench-navigation.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/task_sample_diff/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(60000);
let posts = 0;
const errors = [], evidence = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++; });
async function check(mode) {
    if (mode === 'invalid') {
        await page.getByText('应用样例差异格式未识别，无法确认差异结果。', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('应用样例差异', { exact: true }).count(), 0);
        assert.equal(await page.evaluate(() => window.sampleDiffInjected), undefined);
        return;
    }
    const card = page.getByLabel('应用样例差异', { exact: true });
    await card.waitFor();
    assert.equal(await card.locator('button, a, img, script').count(), 0);
    if (mode === 'empty') {
        await card.getByText('与固定基线没有差异，不代表项目干净或测试通过。', { exact: true }).waitFor();
    } else {
        const body = card.getByLabel('应用样例差异正文');
        assert.ok((await body.textContent()).includes('<img src=x'));
        await body.focus();
        await body.press('ArrowRight');
        assert.ok(await body.evaluate(e => e === document.activeElement && e.scrollWidth > e.clientWidth));
    }
    assert.equal(await page.evaluate(() => window.sampleDiffInjected), undefined);

}
try {
    for (const { mode, workspace_id, task_id } of fixtures) {
        await page.setViewportSize({ width: 1366, height: 900 });
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        const input = page.getByLabel('你的问题');
        await input.fill('[task_sample_diff] 读取应用样例差异');
        const pending = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await input.press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        const runId = response.headers()['x-run-id'];
        await page.getByText('应用样例差异已记录。', { exact: true }).waitFor();
        await openAdvancedDetails(page);
        await check(mode);
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        const run = await stored.json();
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_START').length, 1);
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_RESULT').length, 1);
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/${mode}-${width}.png` });
        }
        const count = posts;
        await page.reload();
        await page.getByText('应用样例差异已记录。', { exact: true }).waitFor();
        await openAdvancedDetails(page);
        await openAdvancedDetails(page);
        const history = page.getByRole('button', { name: `查看运行 ${runId}`, exact: true });
        await history.focus();
        await history.press('Enter');
        await check(mode);
        assert.equal(posts, count);
        const card = page.getByLabel('应用样例差异', { exact: true });
        if (mode !== 'invalid') await card.screenshot({ path: `${output}/${mode}-history-card.png` });
        evidence.push({ mode, run_id: runId, posts, history_no_replay: true });
        console.log(`PASS ${mode}: live/history, desktop widths, keyboard, no replay`);
    }
    assert.deepEqual(errors, []);
    assert.equal(posts, 3);
    await writeFile(`${output}/evidence.json`, JSON.stringify(evidence, null, 4));
} catch (error) {
    console.error(await page.locator("body").innerText());
    await page.screenshot({ path: `${output}/failure.png` });
    throw error;
} finally { await browser.close(); }
