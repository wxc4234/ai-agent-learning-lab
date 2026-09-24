import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/git-status/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(90000);
const errors = [], report = [];
let posts = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
});
try {
    for (const fixture of fixtures) {
        const { mode, workspace_id, task_id } = fixture;
        const success = ['changed', 'empty'].includes(mode);
        const answer = success ? `临时Git样例共有${mode === 'empty' ? 0 : 1}个状态条目；这不是用户项目的Git状态。`
            : `临时Git样例查询失败：${mode === 'missing' ? 'task_git_sample_unavailable' : 'git_sample_unavailable'}，未取得状态。`;
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        const input = page.getByLabel('你的问题');
        await input.fill('[git-status] 查询临时Git样例');
        const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
        await input.press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        await page.getByText(answer, { exact: true }).waitFor();
        console.log(`Observed ${mode}: answer visible`);
        const runId = response.headers()['x-run-id'];
        const expanded = page.getByRole('button', { name: '展开详情', exact: true });
        if (await expanded.isVisible()) await expanded.click();
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const run = await stored.json();
        assert.equal(run.status, 'done');
        const starts = run.events.filter(event => event.event_type === 'TOOL_CALL_START');
        const results = run.events.filter(event => event.event_type === 'TOOL_CALL_RESULT');
        const failures = run.events.filter(event => event.event_type === 'TOOL_CALL_ERROR');
        assert.equal(starts.length, 1);
        assert.equal(starts[0].payload.tool_name, 'git_sample_status');
        assert.equal(results.length, success ? 1 : 0);
        assert.equal(failures.length, success ? 0 : 1);
        await page.getByText('git_sample_status', { exact: true }).waitFor();
        if (success) {
            const raw = results[0].payload.result;
            const result = JSON.parse(raw);
            assert.equal(result.source, 'task_git_sample');
            assert.equal(result.status, 'complete');
            assert.equal(result.entries.length, mode === 'empty' ? 0 : 1);
            if (mode === 'changed') assert.equal(result.entries[0].path, '中文 sample.txt');
            const shown = page.getByLabel('工具结果', { exact: true });
            assert.equal(await shown.textContent(), raw);
            await shown.focus();
            assert.ok(await shown.evaluate(element => element === document.activeElement));
            await page.getByText('成功', { exact: true }).waitFor();
        } else {
            assert.equal(failures[0].payload.details, mode === 'missing' ? 'task_git_sample_unavailable' : 'git_sample_unavailable');
            assert.equal(await page.getByText('成功', { exact: true }).count(), 0);
            assert.equal(await page.getByLabel('工具结果', { exact: true }).count(), 0);
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/${mode}-${width}.png` });
        }
        const count = posts;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        console.log(`Observed ${mode}: answer visible`);
        const details = page.getByRole('button', { name: '展开详情', exact: true });
        if (await details.isVisible()) await details.click();
        await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
        await page.getByText('git_sample_status', { exact: true }).first().waitFor();
        assert.equal(await page.getByText('git_sample_status', { exact: true }).count(), 2);
        if (success) await page.getByText(results[0].payload.result, { exact: true }).waitFor();
        else {
            await page.getByText('工具执行失败', { exact: true }).waitFor();
            assert.equal(await page.getByText('工具执行成功', { exact: true }).count(), 0);
        }
        assert.equal(posts, count);
        const history = await page.request.get(`${base}/api/workspaces/${workspace_id}/tasks/${task_id}/messages`);
        assert.equal(history.status(), 200);
        assert.ok((await history.text()).includes(answer));
        const after = await (await page.request.get(`${base}/api/runs/${runId}`)).json();
        assert.equal(after.events.length, run.events.length);
        report.push({ mode, run_id: Number(runId), result_count: results.length, error_count: failures.length, reload_no_replay: true });
        console.log(`PASS ${mode}: PC result/error, keyboard, two widths, stored history, no replay`);
    }
    assert.equal(posts, 4);
    assert.deepEqual(errors, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(report, null, 4));
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png` }).catch(() => {});
    console.error((await page.locator('body').innerText()).slice(-3000));
    throw error;
} finally {
    await browser.close();
}
