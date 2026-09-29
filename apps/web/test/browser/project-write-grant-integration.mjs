import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/project-write-grant-integration/', import.meta.url));
const fixtures = JSON.parse(await readFile(output + 'fixtures.json', 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(60000);
const errors = [], evidence = {};
let posts = 0, applies = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/write-grant')) posts++;
    if (request.url().endsWith('/apply')) applies++;
});
const endpoint = item => `${base}/api/workspaces/${item.workspace_id}/tasks/${item.task_id}/file-edit-proposals/${item.proposal_id}`;
async function open(item) {
    await page.goto(`${base}/?workspace=${item.workspace_id}&task=${item.task_id}`);
    await page.getByRole('button', { name: '查看改动', exact: true }).click();
    const pane = page.getByRole('complementary', { name: '文件改动', exact: true });
    await pane.locator('summary').filter({ hasText: 'example.txt' }).click();
    await pane.getByRole('button', { name: '查看提案详情', exact: true }).press('Enter');
    await pane.locator('summary').filter({ hasText: '普通项目写入许可' }).press('Enter');
    return pane;
}
async function get(item) {
    const response = await page.request.get(endpoint(item) + '/write-grant');
    assert.equal(response.status(), 200); assert.equal(response.headers()['cache-control'], 'no-store');
    return (await response.json()).grant;
}
try {
    const [normal, unknown, foreign] = fixtures;
    for (const item of [normal, unknown]) {
        const pane = await open(item);
        await pane.getByRole('button', { name: '查询许可状态' }).click();
        await pane.getByText('查询时许可：未发放', { exact: true }).waitFor();
        assert.ok(await pane.getByRole('button', { name: '发放本提案许可', exact: true }).isDisabled());
        await pane.getByRole('button', { name: '批准提案', exact: true }).press('Enter');
        await pane.getByText('已确认批准此提案。应用状态以执行结果为准。', { exact: true }).waitFor();
        assert.equal(await get(item), null);
        await pane.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        if (item === unknown) {
            await page.route('**/write-grant', async route => {
                if (route.request().method() !== 'POST') { await route.continue(); return; }
                // 真正等待BFF→API提交成功，只丢弃返回浏览器的回执。
                const response = await route.fetch(); assert.equal(response.status(), 201);
                await route.abort('failed');
            }, { times: 1 });
        }
        await pane.getByRole('button', { name: '确认发放许可' }).evaluate(button => { button.click(); button.click(); });
        if (item === unknown) {
            await pane.getByText('许可变更结果未确认，请先查询状态，勿重复提交。', { exact: true }).waitFor();
            const count = posts;
            await open(item);
            assert.equal(posts, count);
            await page.getByRole('button', { name: '查询许可状态' }).click();
        }
        await page.getByText('查询时许可：已启用（不代表当前可写）', { exact: true }).waitFor();
        const grant = await get(item); assert.equal(grant.status, 'enabled');
        evidence[item.marker] = grant;
        if (item === normal) {
            const stale = await page.request.post(endpoint(item) + '/write-grant/revoke', {
                headers: { Origin: base }, data: { grant_id: grant.grant_id, revision: 2 },
            });
            assert.equal(stale.status(), 409, await stale.text()); assert.equal((await stale.json()).code, 'project_write_grant_conflict');
            assert.deepEqual(await get(item), grant);
            await page.getByRole('button', { name: '撤销本提案许可', exact: true }).click();
            await page.getByRole('button', { name: '确认撤销许可' }).press('Enter');
            await page.getByText('查询时许可：已撤销，不可重新启用', { exact: true }).waitFor();
            await open(item); await page.getByRole('button', { name: '查询许可状态' }).click();
            await page.getByText('查询时许可：已撤销，不可重新启用', { exact: true }).waitFor();
            assert.equal(await page.getByRole('button', { name: '发放本提案许可', exact: true }).count(), 0);
        }
        assert.equal(await readFile(item.file, 'utf8'), 'old\n');
    }
    for (const suffix of ['', '/revoke']) {
        const denied = await page.request.post(endpoint(foreign) + '/write-grant' + suffix, {
            headers: { Origin: base }, data: suffix ? { grant_id: evidence.normal.grant_id, revision: 1 } : { action: 'grant' },
        });
        assert.equal(denied.status(), 404);
    }
    const deniedRead = await page.request.get(endpoint(foreign) + '/write-grant');
    assert.equal(deniedRead.status(), 404);
    assert.equal(applies, 0); assert.equal(posts, 3); // 两次UI发放和一次UI撤销，不包含APIRequestContext检查。
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: output + `${width}.png`, fullPage: true });
    }
    assert.deepEqual(errors, []);
    await writeFile(output + 'browser.json', JSON.stringify(evidence, null, 2));
    console.log('PASS real PC/BFF/API: approval separate, explicit grant/revoke, lost committed receipt, reload, stale revision and ownership');
} finally { await browser.close(); }
