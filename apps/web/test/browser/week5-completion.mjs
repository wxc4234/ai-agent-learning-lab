import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/week5-completion/', import.meta.url));
const fixture = JSON.parse(await readFile(output + 'fixture.json', 'utf8'));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(45000);
const errors = [], mutations = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (request.method() === 'POST' && /change-sets|owned-area/.test(request.url())) mutations.push({ url: new URL(request.url()).pathname, body: request.postData() }); });
const base = process.env.AUTH_TEST_BASE_URL;
async function pane() {
    await page.getByRole('button', { name: '查看改动', exact: true }).click();
    return page.getByRole('complementary', { name: '文件改动', exact: true });
}
async function apply(panel) {
    await panel.getByRole('button', { name: '查询变更组', exact: true }).click();
    const group = panel.getByRole('region', { name: '通用变更组' }).locator('details').first();
    await group.locator('summary').click();
    await group.getByRole('button', { name: '批准整组', exact: true }).press('Enter');
    await group.getByRole('button', { name: '应用整组修改', exact: true }).click();
    await group.locator('summary').filter({ hasText: '已应用' }).waitFor();
    return group;
}
try {
    await page.goto(`${base}/?workspace=${fixture.workspace_id}&task=${fixture.task_id}`);
    let panel = await pane();
    await panel.getByText('隔离工作区', { exact: true }).click();
    await panel.getByRole('button', { name: '查询隔离工作区', exact: true }).click();
    await panel.getByRole('button', { name: '创建隔离编码任务', exact: true }).click();
    await panel.getByRole('link', { name: '打开隔离任务', exact: true }).click();
    await page.getByLabel('你的问题', { exact: true }).fill('[week5-general] 完成新增、编辑、删除与重命名');
    await page.getByLabel('你的问题', { exact: true }).press('Enter');
    await page.getByText('通用变更组已保存，等待用户审阅。', { exact: true }).waitFor();
    panel = await pane();
    await apply(panel);
    await panel.getByText('隔离工作区', { exact: true }).click();
    await panel.getByRole('button', { name: '查询隔离工作区', exact: true }).click();
    await panel.getByRole('button', { name: '导出为原项目变更组', exact: true }).click();
    await panel.getByText('导出提案已保存，需在原任务审阅并批准。', { exact: true }).waitFor();
    await panel.getByRole('link', { name: '返回原任务审阅', exact: true }).click();
    panel = await pane();
    const group = await apply(panel);
    await group.getByRole('button', { name: '检查并恢复整组原文', exact: true }).click();
    await group.locator('summary').filter({ hasText: '已恢复原文' }).waitFor();
    await page.screenshot({ path: output + 'pc-1366.png', fullPage: true });
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.screenshot({ path: output + 'pc-1920.png', fullPage: true });
    assert.deepEqual(errors, []);
    assert.equal(mutations.filter(item => JSON.parse(item.body).action === 'apply').length, 2);
    await writeFile(output + 'browser.json', JSON.stringify({ errors, mutations, widths: [1366,1920], model: 'deterministic test double' }));
} finally { await browser.close(); }
