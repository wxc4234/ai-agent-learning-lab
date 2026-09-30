import { openAdvancedDetails } from './workbench-navigation.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/vault-search/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(90000);
const errors = [], report = [], directApiRequests = [];
let posts = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
    if (new URL(request.url()).port === '18000') directApiRequests.push(request.url());
});
const reasonLabels = {
    inventory_truncated: '目录清单不完整，部分目录或文件未进入本次检索。',
    file_budget: '已达到 20 个文件的检索上限。',
    match_budget: '已达到 50 个命中行的返回上限。',
};

async function verifyCard(result) {
    const card = page.getByLabel('Vault 笔记检索', { exact: true });
    await card.waitFor();
    const coverage = result.matches.length === 0
        ? (result.truncated ? '当前已检索部分没有匹配，仍有内容未完整检查。' : '本次检索范围内没有匹配。')
        : (result.truncated ? '检索不完整，以下是本次已取得的匹配片段。' : `本次检索范围内找到 ${result.matches.length} 个命中行。`);
    assert.equal(await card.getByLabel('检索覆盖状态').textContent(), coverage);
    for (const reason of result.incomplete_reasons) await card.getByText(reasonLabels[reason], { exact: true }).waitFor();
    assert.equal(await card.locator('ol > li').count(), result.matches.length);
    for (const match of result.matches) {
        const citation = `${match.source.relative_path}:${match.source.start_line}`;
        await card.getByText(citation, { exact: true }).waitFor({ state: 'attached' });
        const snippet = card.getByLabel(`片段 ${citation}`, { exact: true });
        assert.equal(await snippet.textContent(), match.snippet);
    }
    assert.equal(await card.locator('img, script, a, button').count(), 0);
    assert.equal(await page.evaluate(() => window.vaultInjected), undefined);
    assert.equal(await card.getByText('片段已裁剪，不是完整命中行。', { exact: true }).count(), result.matches.filter(match => match.snippet_truncated).length);
    if (result.matches.length) {
        const list = card.getByLabel('笔记命中列表', { exact: true });
        await list.focus();
        assert.ok(await list.evaluate(element => element === document.activeElement));
        if (result.matches.length === 50) {
            assert.ok(await list.evaluate(element => element.scrollHeight > element.clientHeight));
            await list.press('ArrowDown');
            await page.waitForFunction(() => document.activeElement.scrollTop > 0);
            await list.evaluate(element => { element.scrollTop = 0; });
        }
        const first = result.matches[0];
        const snippet = card.getByLabel(`片段 ${first.source.relative_path}:${first.source.start_line}`, { exact: true });
        await snippet.focus();
        assert.ok(await snippet.evaluate(element => element === document.activeElement));
        const summary = card.locator('summary').first();
        await summary.focus();
        if (!await summary.evaluate(element => element.parentElement.open)) await summary.press('Enter');
        assert.ok(await summary.evaluate(element => element.parentElement.open));
        assert.equal(await summary.locator('..').locator('code').textContent(), first.source.sha256);
    }
    return card;
}

try {
    for (const fixture of fixtures) {
        const { mode, workspace_id, task_id } = fixture;
        const success = mode !== 'error';
        const answer = `Vault 浏览器验收 ${mode} 已完成。`;
        await page.setViewportSize({ width: 1366, height: 900 });
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        const input = page.getByLabel('你的问题', { exact: true });
        await input.waitFor();
        // 数据流向说明必须在用户按下发送前出现，并关联输入框。
        await page.getByText('使用笔记检索时，命中片段会发送给你配置的模型。', { exact: true }).waitFor();
        assert.equal(await input.getAttribute('aria-describedby'), 'vault-model-notice');
        await input.fill(`[vault-search] [${mode}] 检索当前项目笔记`);
        const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
        await input.press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        await page.getByText(answer, { exact: true }).waitFor();
        const runId = response.headers()['x-run-id'];
        assert.ok(runId);
        await openAdvancedDetails(page);
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const run = await stored.json();
        assert.equal(run.status, 'done');
        const starts = run.events.filter(event => event.event_type === 'TOOL_CALL_START');
        const results = run.events.filter(event => event.event_type === 'TOOL_CALL_RESULT');
        const failures = run.events.filter(event => event.event_type === 'TOOL_CALL_ERROR');
        assert.equal(starts.length, 1);
        assert.equal(starts[0].payload.tool_name, 'search_vault');
        assert.equal(results.length, success ? 1 : 0);
        assert.equal(failures.length, success ? 0 : 1);
        let result;
        if (success) {
            result = JSON.parse(results[0].payload.result);
            assert.equal(result.workspace_id, workspace_id);
            assert.equal(result.task_id, task_id);
            assert.deepEqual(result.incomplete_reasons, ({
                file_budget: ['file_budget'], match_budget: ['match_budget'], inventory_budget: ['inventory_truncated'],
            })[mode] || []);
            assert.equal(result.matches.length, ({ match: 1, empty: 0, file_budget: 0, match_budget: 50, inventory_budget: 1, snippet: 1 })[mode]);
            if (mode === 'match') {
                assert.equal(result.matches[0].source.relative_path, 'notes/中文笔记.md');
                assert.equal(result.matches[0].source.start_line, 2);
                assert.equal(result.matches[0].column_number, 2);
            }
            if (mode === 'snippet') {
                assert.equal(result.truncated, false);
                assert.equal(result.matches[0].snippet_truncated, true);
            }
            await verifyCard(result);
            await page.getByText('调用完成', { exact: true }).waitFor();
        } else {
            assert.equal(failures[0].payload.details, 'vault_search_unavailable');
            await page.getByText('检索失败，不能据此判断没有相关笔记。', { exact: true }).waitFor();
            assert.equal(await page.getByLabel('Vault 笔记检索', { exact: true }).count(), 0);
            assert.equal(await page.getByText('调用完成', { exact: true }).count(), 0);
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            if (success) await page.getByLabel('Vault 笔记检索', { exact: true }).scrollIntoViewIfNeeded();
            await page.screenshot({ path: `${output}/${mode}-${width}.png` });
        }
        const count = posts;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        await openAdvancedDetails(page);
        await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
        if (success) await verifyCard(result);
        else {
            await page.getByText('检索失败，不能据此判断没有相关笔记。', { exact: true }).waitFor();
            assert.equal(await page.getByLabel('Vault 笔记检索', { exact: true }).count(), 0);
        }
        await page.screenshot({ path: `${output}/${mode}-restored.png` });
        assert.equal(posts, count);
        const after = await (await page.request.get(`${base}/api/runs/${runId}`)).json();
        assert.equal(after.events.length, run.events.length);
        const history = await page.request.get(`${base}/api/workspaces/${workspace_id}/tasks/${task_id}/messages`);
        assert.equal(history.status(), 200);
        assert.ok((await history.text()).includes(answer));
        report.push({ mode, run_id: Number(runId), result_count: results.length, error_count: failures.length, reload_no_replay: true });
        console.log(`PASS ${mode}: actual Vault, two PC widths, keyboard, stored history, no replay`);
    }
    assert.equal(posts, 7);
    assert.deepEqual(errors, []);
    assert.deepEqual(directApiRequests, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(report, null, 4));
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png` }).catch(() => {});
    console.error((await page.locator('body').innerText()).slice(-3000));
    throw error;
} finally {
    await browser.close();
}
