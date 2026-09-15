import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const artifacts = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(artifacts, { recursive: true });
const directory = await mkdtemp('/private/tmp/agent-directory-');
const root = process.env.BROWSER_TEST_DIRECTORY || await realpath(directory);
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
let passed = 0;
const failures = [];
const item = n => ({ external_id: n.toString(16).padStart(32, '0'), name: `项目 ${n}`, created_at: '2026-09-15T08:00:00Z' });
const data = (n, root_path = null) => ({ external_id: item(n).external_id, name: item(n).name, root_path });
const pattern = '**/api/workspaces/*/directory{,/select}';
const panel = page => page.getByRole('region', { name: '项目目录', exact: true });
const select = page => panel(page).getByRole('button', { name: '选择项目目录', exact: true });
const fulfill = (route, payload, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
async function scenario(name, run) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
        await run(page);
        assert.deepEqual(errors, []);
        passed++;
        console.log(`PASS ${name}`);
    } catch (error) {
        failures.push(name);
        console.error(`FAIL ${name}: ${error.stack}`);
        await page.screenshot({ path: `${artifacts}/directory-fail-${failures.length}.png`, timeout: 10000 });
    } finally { await context.close(); }
}
async function mockList(page) {
    await page.route('**/api/workspaces?limit=20', route => fulfill(route, { items: [item(1), item(2)], has_more: false }));
}
async function choose(page, n = 1) {
    await page.getByRole('button', { name: item(n).name, exact: true }).click();
}
async function retry(page) {
    await panel(page).getByRole('button', { name: '重新读取', exact: true }).click();
}
async function create(page, name) {
    const response = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(response.status(), 201);
    return response.json();
}
try {
    // 真实 BFF/API/隔离 PostgreSQL：路径拒绝、保存、刷新和 PC 布局。
    await scenario('real binding, cancel, persistence and PC layout', async page => {
        await create(page, '真实目录项目');
        await page.goto(base);
        await page.getByRole('button', { name: '真实目录项目', exact: true }).click();
        await select(page).waitFor();
        assert.equal(await panel(page).locator('input').count(), 0);
        await select(page).click();
        await panel(page).getByText('已取消选择，项目未作更改。', { exact: true }).waitFor();
        await select(page).click();
        await panel(page).getByText(root, { exact: true }).waitFor();
        await page.reload();
        await page.getByRole('button', { name: '真实目录项目', exact: true }).click();
        await panel(page).getByText(root, { exact: true }).waitFor();
        await page.getByLabel('你的问题').fill('目录操作保留聊天草稿');
        await page.getByRole('button', { name: '收起导航' }).click();
        await page.getByRole('button', { name: '展开导航' }).click();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '目录操作保留聊天草稿');
        for (const [width, height] of [[1366, 768], [1920, 1080]]) {
            await page.setViewportSize({ width, height });
            const nav = page.locator('#workbench-navigation');
            assert.ok(await nav.evaluate(n => n.scrollWidth <= n.clientWidth + 1));
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1));
            const box = await page.getByLabel('你的问题').boundingBox();
            assert.ok(box.width > 200 && box.y + box.height <= height);
            await page.screenshot({ path: `${artifacts}/directory-${width}.png`, timeout: 10000, animations: 'disabled' });
        }
    });
    // selection POST 真正提交后丢弃响应，再通过 GET 恢复，不重复写入。
    await scenario('real committed selection POST with lost response recovers through GET', async page => {
        await create(page, '响应丢失项目');
        let writes = 0;
        await page.route(pattern, async route => {
            if (route.request().method() !== 'POST') return route.continue();
            writes++;
            const response = await route.fetch();
            assert.equal(response.status(), 200);
            await route.abort('failed');
        });
        await page.goto(base);
        await page.getByRole('button', { name: '响应丢失项目', exact: true }).click();
        await select(page).click();
        await panel(page).getByText('目录选择结果未确认，请重新读取当前保存状态。', { exact: true }).waitFor();
        assert.equal(await select(page).count(), 0);
        await retry(page);
        await panel(page).getByText(root, { exact: true }).waitFor();
        assert.equal(writes, 1);
    });
    await scenario('invalid GET never becomes unbound and retry restores state', async page => {
        await mockList(page);
        const cases = [
            [200, {}], [200, { ...data(1), root_path: undefined }], [200, data(2)],
            [200, { ...data(1), root_path: '' }], [200, { ...data(1), root_path: 3 }],
            [200, { ...data(1), name: '😀'.repeat(101) }], [200, null], [503, {}],
        ];
        let current = cases[0];
        await page.route(pattern, route => fulfill(route, current[1], current[0]));
        await page.goto(base);
        await choose(page);
        for (let index = 0; index < cases.length; index++) {
            if (index) { current = cases[index]; await retry(page); }
            await panel(page).getByRole('alert').waitFor();
            assert.equal(await select(page).count(), 0);
            assert.equal(await panel(page).getByText('当前未绑定目录', { exact: true }).count(), 0);
        }
        current = [200, data(1)];
        await retry(page);
        await select(page).waitFor();
    });
    await scenario('selection POST rejection, malformed success, conflict and network failure', async page => {
        await mockList(page);
        let mode = 'reject';
        let writes = 0;
        await page.route(pattern, route => {
            if (route.request().method() === 'GET') return fulfill(route, data(1));
            writes++;
            assert.deepEqual(route.request().postDataJSON(), {});
            if (mode === 'reject') return fulfill(route, { code: 'invalid_directory_path', message: 'PRIVATE' }, 422);
            if (mode === 'conflict') return fulfill(route, { code: 'workspace_already_bound' }, 409);
            if (mode === 'network') return route.abort();
            return fulfill(route, data(1));
        });
        await page.goto(base);
        await choose(page);
        await select(page).click();
        await panel(page).getByText('所选目录路径不符合要求，请重新选择。', { exact: true }).waitFor();
        assert.equal(await page.getByText('PRIVATE').count(), 0);
        for (mode of ['malformed', 'conflict', 'network']) {
            await select(page).click();
            await panel(page).getByText('目录选择结果未确认，请重新读取当前保存状态。', { exact: true }).waitFor();
            await retry(page);
            await select(page).waitFor();
        }
        assert.equal(writes, 4);
    });
    // 强制忽略 abort 的旧 fetch 晚返回，验证组件身份检查而非只依赖网络取消。
    await scenario('late old GET and selection POST cannot overwrite another project', async page => {
        await mockList(page);
        await page.addInitScript(() => {
            const original = window.fetch;
            window.fetch = (url, init) => {
                if (String(url).includes('/directory') && window.holdDirectory) {
                    window.heldCount = (window.heldCount || 0) + 1;
                    window.heldDirectory = { url: String(url), method: init.method };
                    return new Promise(resolve => { window.releaseDirectory = () => resolve(new Response(JSON.stringify({ external_id: '1'.padStart(32, '0'), name: '旧项目', root_path: '/old-path' }), { headers: { 'Content-Type': 'application/json' } })); });
                }
                return original(url, init);
            };
        });
        await page.route(pattern, route => fulfill(route, data(route.request().url().includes(item(2).external_id) ? 2 : 1)));
        await page.goto(base);
        await page.evaluate(() => { window.holdDirectory = true; });
        await choose(page, 1);
        await panel(page).getByText('正在读取目录状态…', { exact: true }).waitFor();
        await page.evaluate(() => { window.holdDirectory = false; });
        await choose(page, 2);
        await select(page).waitFor();
        await page.evaluate(() => window.releaseDirectory());
        assert.equal(await panel(page).getByText('/old-path', { exact: true }).count(), 0);
        await choose(page, 1);
        await select(page).waitFor();
        await page.evaluate(() => { window.holdDirectory = true; window.heldCount = 0; });
        // 同一事件循环连续提交，验证 React 按钮更新前 ref 已阻止重复请求。
        await select(page).evaluate(n => { n.click(); n.click(); });
        await panel(page).getByText('请在系统窗口中选择项目目录…', { exact: true }).waitFor();
        assert.equal((await page.evaluate(() => window.heldDirectory)).method, 'POST');
        assert.equal(await page.evaluate(() => window.heldCount), 1);
        await page.evaluate(() => { window.holdDirectory = false; });
        await choose(page, 2);
        await select(page).waitFor();
        await page.evaluate(() => window.releaseDirectory());
        assert.equal(await panel(page).getByText('/old-path', { exact: true }).count(), 0);
        await page.getByLabel('你的问题').fill('项目切换保留草稿');
        await page.getByRole('button', { name: '收起导航' }).click();
        await page.getByRole('button', { name: '展开导航' }).click();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '项目切换保留草稿');
    });
    await scenario('GET and selection POST timeout during response body read', async page => {
        await mockList(page);
        await page.addInitScript(() => {
            const original = window.fetch;
            window.fetch = async (url, init) => {
                if (String(url).includes('/directory') && window.timeoutDirectory) {
                    // 先收到响应头，正文保持未完成，超时必须覆盖 response.json。
                    return { status: 200, json: () => new Promise((resolve, reject) => {
                        init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
                    }) };
                }
                return original(url, init);
            };
            const timeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => timeout.call(AbortSignal, (ms === 15000 || ms === 140000) && window.timeoutDirectory ? 200 : ms);
        });
        await page.route(pattern, route => fulfill(route, data(1)));
        await page.goto(base);
        await page.getByRole('button', { name: item(1).name, exact: true }).waitFor();
        await page.evaluate(() => { window.timeoutDirectory = true; });
        await choose(page);
        await panel(page).getByText('目录状态读取失败，请重试。', { exact: true }).waitFor();
        await page.evaluate(() => { window.timeoutDirectory = false; });
        await retry(page);
        await select(page).waitFor();
        await page.evaluate(() => { window.timeoutDirectory = true; });
        await select(page).click();
        await panel(page).getByText('目录选择结果未确认，请重新读取当前保存状态。', { exact: true }).waitFor();
        await page.evaluate(() => { window.timeoutDirectory = false; });
        await retry(page);
        await select(page).waitFor();
        assert.equal(await select(page).isEnabled(), true);
    });
    console.log(`Directory panel: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally {
    await browser.close();
    await rm(directory, { recursive: true, force: true });
}
