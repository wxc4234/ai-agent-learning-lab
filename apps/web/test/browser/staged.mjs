import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/staged/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [], results = [];
page.on('pageerror', error => errors.push(error.message));
async function open(item) {
    await page.goto(`${base}/?workspace=${item.workspace_id}&task=${item.task_id}`);
    await page.getByLabel('项目更多操作').click();
    await page.getByRole('button', { name: '查看改动', exact: true }).click();
    return page.getByRole('region', { name: 'Git暂存差异', exact: true });
}
try {
    for (const item of fixtures) {
        const panel = await open(item);
        await panel.getByRole('button', { name: '查询暂存差异' }).press('Enter');
        const text = { modified: 'file.txt', empty: '本次未发现暂存差异', missing: 'HEAD 或暂存区信息不完整，无法比较。', unsupported: '项目格式或大小暂不支持，未取得暂存差异' }[item.kind];
        await panel.getByText(text, { exact: true }).waitFor();
        assert.ok(await panel.getByText('对比 HEAD 与暂存区；不包含未暂存改动，不代表项目干净。').isVisible());
        await page.screenshot({ path: `${output}/${item.kind}-1366.png`, fullPage: true });
        results.push(item.kind);
    }
    await page.setViewportSize({ width: 1920, height: 1080 });
    const panel = await open(fixtures[0]);
    await panel.getByRole('button', { name: '查询暂存差异' }).click();
    await panel.getByText('file.txt', { exact: true }).waitFor();
    await page.screenshot({ path: `${output}/modified-1920.png`, fullPage: true });
    // 仅延迟响应测试取消；前面四类结果均来自真实BFF/API。
    await page.route('**/git/staged?*', async route => { await new Promise(resolve => setTimeout(resolve, 1500)); await route.continue().catch(() => {}); });
    await panel.getByRole('button', { name: '查询暂存差异' }).click();
    await panel.getByRole('button', { name: '取消查询' }).click();
    await panel.getByText('查询已取消或超时，当前结果未知').waitFor();
    assert.equal(await panel.getByText('file.txt', { exact: true }).count(), 0);
    await page.unroute('**/git/staged?*');
    assert.deepEqual(errors, []);
    await writeFile(`${output}/report.json`, JSON.stringify({ results, widths: [1366, 1920], canceled: true, errors }, null, 2));
} finally { await browser.close(); }
