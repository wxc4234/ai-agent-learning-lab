import { openAdvancedDetails } from './workbench-navigation.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/git-diff/', import.meta.url));
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
        const success = ['worktree', 'staged', 'empty'].includes(mode);
        const scope = ['staged', 'missing-head'].includes(mode) ? 'staged' : 'worktree';
        const answer = success ? `样例${scope}差异已读取；这不是用户项目。`
            : `临时Git样例查询失败：${mode === 'missing' ? 'task_git_sample_unavailable' : 'git_diff_command_failed'}，未取得差异。`;
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        const input = page.getByLabel('你的问题');
        await input.fill(`[git-diff] [${scope}] 查询临时Git样例`);
        const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
        await input.press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        await page.getByText(answer, { exact: true }).waitFor();
        console.log(`Observed ${mode}: answer visible`);
        const runId = response.headers()['x-run-id'];
        await openAdvancedDetails(page);
        // 当前工作台把工具结果放在可折叠诊断区，先用键盘展开。
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const run = await stored.json();
        assert.equal(run.status, 'done');
        const starts = run.events.filter(event => event.event_type === 'TOOL_CALL_START');
        const results = run.events.filter(event => event.event_type === 'TOOL_CALL_RESULT');
        const failures = run.events.filter(event => event.event_type === 'TOOL_CALL_ERROR');
        assert.equal(starts.length, 1);
        assert.equal(starts[0].payload.tool_name, 'git_sample_diff');
        assert.equal(results.length, success ? 1 : 0);
        assert.equal(failures.length, success ? 0 : 1);
        await page.getByText('git_sample_diff', { exact: true }).waitFor();
        if (success) {
            const raw = results[0].payload.result;
            const result = JSON.parse(raw);
            assert.equal(result.source, 'task_git_sample');
            assert.equal(result.status, 'complete');
            assert.equal(result.scope, scope);
            const card = page.getByLabel('Git 样例差异', { exact: true });
            await card.waitFor();
            await card.getByText(scope === 'worktree' ? '未暂存差异 · 暂存区 → 工作区' : '已暂存差异 · HEAD → 暂存区', { exact: true }).waitFor();
            if (mode === 'empty') {
                await card.getByText('所选范围没有差异，不代表整个仓库干净。', { exact: true }).waitFor();
                assert.equal(await card.getByLabel('Git 差异正文').count(), 0);
            } else {
                const shown = card.getByLabel('Git 差异正文', { exact: true });
                assert.equal(await shown.textContent(), result.diff);
                await shown.focus();
                assert.ok(await shown.evaluate(element => element === document.activeElement));
                assert.equal(await card.locator('img, script').count(), 0);
                assert.equal(await page.evaluate(() => window.diffInjected), undefined);
                if (mode === 'worktree') {
                    await shown.press('ArrowRight');
                    assert.ok(await shown.evaluate(element => element.scrollWidth > element.clientWidth));
                }
            }
            await page.getByText('成功', { exact: true }).waitFor();
        } else {
            assert.equal(failures[0].payload.details, mode === 'missing' ? 'task_git_sample_unavailable' : 'git_diff_command_failed');
            assert.equal(await page.getByText('成功', { exact: true }).count(), 0);
            assert.equal(await page.getByLabel('Git 样例差异', { exact: true }).count(), 0);
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
        await openAdvancedDetails(page);
        await openAdvancedDetails(page);
        await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
        await page.getByText('git_sample_diff', { exact: true }).first().waitFor();
        assert.equal(await page.getByText('git_sample_diff', { exact: true }).count(), 2);
        if (success) {
            const card = page.getByLabel('Git 样例差异', { exact: true });
            await card.waitFor();
            const diff = JSON.parse(results[0].payload.result).diff;
            if (diff) assert.equal(await card.getByLabel('Git 差异正文').textContent(), diff);
            else await card.getByText('所选范围没有差异，不代表整个仓库干净。', { exact: true }).waitFor();
        }
        else {
            await page.getByText('工具执行失败', { exact: true }).waitFor();
            assert.equal(await page.getByText('工具执行成功', { exact: true }).count(), 0);
        }
        if (success) await page.getByLabel('Git 样例差异', { exact: true }).scrollIntoViewIfNeeded();
        else await page.getByText('工具执行失败', { exact: true }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${output}/${mode}-restored.png` });
        assert.equal(posts, count);
        const history = await page.request.get(`${base}/api/workspaces/${workspace_id}/tasks/${task_id}/messages`);
        assert.equal(history.status(), 200);
        assert.ok((await history.text()).includes(answer));
        const after = await (await page.request.get(`${base}/api/runs/${runId}`)).json();
        assert.equal(after.events.length, run.events.length);
        report.push({ mode, run_id: Number(runId), result_count: results.length, error_count: failures.length, reload_no_replay: true });
        console.log(`PASS ${mode}: PC result/error, keyboard, two widths, stored history, no replay`);
    }
    assert.equal(posts, 5);
    assert.deepEqual(errors, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(report, null, 4));
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png` }).catch(() => {});
    console.error((await page.locator('body').innerText()).slice(-3000));
    throw error;
} finally {
    await browser.close();
}
