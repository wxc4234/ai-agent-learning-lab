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
}const listPattern = '**/api/workspaces?limit=20';
const item = (n, name = `项目 ${n}`) => ({ external_id: n.toString(16).padStart(32, '0'), name, created_at: '2026-09-15T08:00:00Z' });
async function ready(page) {
    await page.getByRole('button', { name: '刷新项目', exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === '刷新项目' && !b.disabled));
}
try {
    await scenario('sidebar real empty create select refresh and PC', async page => {
        await page.goto(base);
        await page.getByText('还没有工作空间，可以先创建一个。', { exact: true }).waitFor();
        await page.getByRole('link', { name: '创建工作空间', exact: true }).click();
        await page.getByLabel('工作空间名称', { exact: true }).fill('真实侧栏项目');
        await page.getByRole('button', { name: '创建工作空间', exact: true }).click();
        await page.getByText('工作空间已创建', { exact: true }).waitFor();
        await page.goto(base);
        const project = page.getByRole('button', { name: '真实侧栏项目', exact: true });
        await project.waitFor();
        await page.getByLabel('你的问题').fill('不丢失的草稿');
        await project.focus();
        await page.keyboard.press('Enter');
        assert.equal(await project.getAttribute('aria-pressed'), 'true');
        await page.getByRole('region', { name: '选中项目资料' }).waitFor();
        await page.getByRole('button', { name: '刷新项目' }).click();
        await ready(page);
        assert.equal(await project.getAttribute('aria-pressed'), 'true');
        await page.getByRole('button', { name: '收起导航' }).click();
        await geometry(page, false, true);
        await page.getByRole('button', { name: '展开导航' }).click();
        assert.equal(await project.getAttribute('aria-pressed'), 'true');
        assert.equal(await page.getByLabel('你的问题').inputValue(), '不丢失的草稿');
        for (const [width, height] of [[1366, 768], [1920, 1080]]) {
            await page.setViewportSize({ width, height });
            await geometry(page, true, true);
            await page.screenshot({ path: `/private/tmp/agent-ui-preview/sidebar-${width}.png` });
        }
    });
    await scenario('sidebar errors retry malformed response are not empty', async page => {
        let mode = 'error';
        await page.route(listPattern, route => route.fulfill({ status: mode === 'error' ? 503 : 200, contentType: 'application/json', body: JSON.stringify(mode === 'error' ? {} : mode === 'malformed' ? { items: [], has_more: true } : { items: [], has_more: false }) }));
        await page.goto(base);
        await page.getByRole('alert').waitFor();
        assert.equal(await page.getByText('还没有工作空间，可以先创建一个。', { exact: true }).count(), 0);
        mode = 'empty';
        await page.getByRole('button', { name: '重试加载' }).click();
        await page.getByText('还没有工作空间，可以先创建一个。', { exact: true }).waitFor();
        mode = 'malformed';
        await page.getByRole('button', { name: '刷新项目' }).click();
        await page.getByRole('alert').waitFor();
        assert.equal(await page.getByText('还没有工作空间，可以先创建一个。', { exact: true }).count(), 0);
    });
    await scenario('sidebar truncated names selection replacement and no chat reset', async page => {
        let items = Array.from({ length: 20 }, (_, n) => item(n + 1, n === 0 ? '长项目名称'.repeat(20) : `项目 ${n + 1}`));
        let more = true;
        await page.route(listPattern, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items, has_more: more }) }));
        await page.goto(base);
        const list = page.getByRole('list', { name: '工作空间列表' });
        await list.waitFor();
        assert.equal(await list.getByRole('button').count(), 20);
        await page.getByText('当前显示最近 20 个项目。', { exact: true }).waitFor();
        await list.getByRole('button').first().click();
        await page.getByLabel('你的问题').fill('选中项目不改变输入');
        await geometry(page, true, true);
        const nav = page.locator('#workbench-navigation');
        assert.ok(await nav.evaluate(n => n.scrollWidth <= n.clientWidth + 1));
        items = [item(2, '更新后的项目')]; more = false;
        await page.getByRole('button', { name: '刷新项目' }).click();
        await ready(page);
        assert.equal(await page.getByRole('region', { name: '选中项目资料' }).count(), 0);
        await page.getByRole('button', { name: '更新后的项目', exact: true }).click();
        await page.getByRole('region', { name: '选中项目资料' }).waitFor();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '选中项目不改变输入');
    });
    await scenario('sidebar inflight duplicate refresh and unmount', async page => {
        let calls = 0;
        let release;
        let started;
        const pending = new Promise(resolve => { release = resolve; });
        const received = new Promise(resolve => { started = resolve; });
        await page.route(listPattern, async route => {
            calls++;
            if (calls === 1) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [item(1)], has_more: false }) });
            started();
            await pending;
            try { await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [item(2)], has_more: false }) }); } catch { /* 页面离开后请求已经取消。 */ }
        });
        await page.goto(base);
        await ready(page);
        const refresh = page.getByRole('button', { name: '刷新项目', exact: true });
        await refresh.click();
        await received;
        assert.equal(await refresh.isDisabled(), true);
        await refresh.dispatchEvent('click');
        assert.equal(calls, 2);
        await page.getByRole('link', { name: '创建工作空间', exact: true }).click();
        await page.getByLabel('工作空间名称', { exact: true }).waitFor();
        release();
        await page.unroute(listPattern);
        await page.goto(base);
        await page.getByRole('button', { name: '真实侧栏项目', exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: '项目 2', exact: true }).count(), 0);
    });
    console.log(`Sidebar: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }
