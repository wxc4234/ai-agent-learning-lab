import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const failures = [];
let passed = 0;
await mkdir('/private/tmp/agent-ui-preview', { recursive: true });
async function scenario(name, run) {
    if (process.env.WORKBENCH_TEST_FILTER && !name.includes(process.env.WORKBENCH_TEST_FILTER)) return;
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, colorScheme: 'light' });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    try { await run(page); passed++; console.log(`PASS ${name}`); }
    catch (error) {
        failures.push(name);
        console.error(`FAIL ${name}: ${error.message}`);
        await page.screenshot({ path: `/private/tmp/agent-ui-preview/workbench-fail-${failures.length}.png` });
    } finally { await context.close(); }
}
async function geometry(page, left, right) {
    const box = await page.getByRole('main').boundingBox();
    const width = page.viewportSize().width;
    assert.ok(box.width >= width - (left ? 240 : 0) - (right ? 300 : 0) - 2, `middle width ${box.width}`);
    assert.ok(Math.abs(box.x - (left ? 240 : 0)) < 2, `middle x ${box.x}`);
    const input = await page.getByLabel('你的问题').boundingBox();
    assert.ok(input.width > 200 && input.y + input.height <= page.viewportSize().height);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1));
}
try {
    await scenario('PC layout and keyboard sidebar toggles preserve draft', async page => {
        await page.goto(base);
        await page.getByLabel('你的问题').fill('保留输入');
        for (const [width, height] of [[1366, 768], [1920, 1080]]) {
            await page.setViewportSize({ width, height });
            await geometry(page, true, true);
            await page.screenshot({ path: `/private/tmp/agent-ui-preview/workbench-${width}.png` });
        }
        await page.getByRole('button', { name: '收起导航' }).focus();
        await page.keyboard.press('Enter');
        await geometry(page, false, true);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '保留输入');
        await page.getByRole('button', { name: '收起详情' }).click();
        await geometry(page, false, false);
        await page.getByRole('button', { name: '展开导航' }).click();
        await geometry(page, true, false);
        await page.getByRole('button', { name: '展开详情' }).click();
        await geometry(page, true, true);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '保留输入');
    });
    await scenario('real chat cancellation and retry', async page => {
        await page.goto(base);
        await page.getByLabel('你的问题').fill('本机工作台聊天');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('隔离模型：认证聊天成功。', { exact: true }).waitFor({ timeout: 30000 });
        await page.getByLabel('你的问题').fill('[cancel-test] 保留运行');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('calculate_rectangle_area', { exact: true }).waitFor();
        await page.getByRole('button', { name: '收起导航' }).click();
        await page.getByRole('button', { name: '收起详情' }).click();
        await geometry(page, false, false);
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
        await page.getByRole('button', { name: '展开导航' }).click();
        await page.getByRole('button', { name: '展开详情' }).click();
        const result = page.waitForResponse(r => r.url().includes('/cancel'));
        await page.getByRole('button', { name: '停止生成', exact: true }).click();
        assert.equal((await result).status(), 204);
        await page.getByRole('button', { name: '重新生成', exact: true }).click();
        await page.getByText('隔离模型：认证聊天成功。', { exact: true }).waitFor({ timeout: 30000 });
        await geometry(page, true, true);
        await page.screenshot({ path: '/private/tmp/agent-ui-preview/workbench-result.png' });
    });
    await scenario('long reply scrolls independently and dark PC layout', async page => {
        await page.goto(base);
        await page.getByLabel('你的问题').fill('[workbench-long] 长回复');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('第 199 行：工作台长回复滚动验证。', { exact: false }).waitFor();
        const region = page.getByRole('region', { name: '对话内容' });
        assert.ok(await region.evaluate(node => node.scrollHeight > node.clientHeight));
        const before = await page.getByLabel('你的问题').boundingBox();
        await region.evaluate(node => { node.scrollTop = node.scrollHeight; });
        const after = await page.getByLabel('你的问题').boundingBox();
        assert.equal(before.y, after.y);
        await geometry(page, true, true);
        await page.screenshot({ path: '/private/tmp/agent-ui-preview/workbench-long.png' });
        await page.emulateMedia({ colorScheme: 'dark' });
        await page.setViewportSize({ width: 1920, height: 1080 });
        await geometry(page, true, true);
        await page.screenshot({ path: '/private/tmp/agent-ui-preview/workbench-dark.png' });
    });
    console.log(`Workbench: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }
