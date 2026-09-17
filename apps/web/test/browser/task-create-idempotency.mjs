import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const artifacts = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
let passed = 0;
const failures = [];
async function scenario(name, run) {
    if (process.env.BROWSER_SCENARIO && !process.env.BROWSER_SCENARIO.split(",").some(prefix => name.startsWith(prefix))) return;
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try { await run(page); assert.deepEqual(errors, []); passed++; console.log(`PASS ${name}`); }
    catch (error) { failures.push(name); console.error(`FAIL ${name}: ${error.stack}`); await page.screenshot({ path: `${artifacts}/creation-failure-${failures.length}.png` }); }
    finally { await context.close(); }
}
async function project(page, name) {
    const response = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(response.status(), 201);
    return response.json();
}
async function draft(page, name) {
    await page.getByRole('button', { name: `在 ${name} 新建任务`, exact: true }).click();
}
async function submit(page, value) {
    await page.getByLabel('你的问题').fill(value);
    await page.getByLabel('你的问题').evaluate(node => { node.form.requestSubmit(); node.form.requestSubmit(); });
}
try {
    await scenario('committed response lost, switch drafts, replay once, explicit message send', async page => {
        const a = await project(page, '幂等甲');
        await project(page, '幂等乙');
        const bodies = [];
        let chats = 0;
        page.on('request', req => { if (req.url().endsWith('/api/chat/stream')) chats++; });
        await page.route(`**/api/workspaces/${a.external_id}/tasks`, async route => {
            if (route.request().method() !== 'POST') return route.continue();
            bodies.push(route.request().postDataJSON());
            const response = await route.fetch();
            assert.equal(response.status(), 201);
            if (bodies.length === 1) return route.abort('failed');
            return route.fulfill({ response });
        });
        await page.goto(base);
        await draft(page, '幂等甲');
        await submit(page, '这是需要保留的首条消息');
        await page.getByRole('button', { name: '重试创建', exact: true }).waitFor();
        assert.equal(chats, 0);
        assert.equal(bodies.length, 1);
        assert.match(bodies[0].request_key, /^[a-f0-9]{32}$/);
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
        await page.screenshot({ path: `${artifacts}/creation-uncertain-1366.png` });
        await draft(page, '幂等乙');
        assert.equal(await page.getByLabel('你的问题').inputValue(), '');
        await draft(page, '幂等甲');
        assert.equal(await page.getByLabel('你的问题').inputValue(), '这是需要保留的首条消息');
        await page.getByRole('button', { name: '重试创建', exact: true }).evaluate(node => { node.click(); node.click(); });
        await page.getByText('任务已找回。请查看历史并确认输入，再点击发送；本次没有自动发送消息。', { exact: true }).waitFor();
        await page.waitForFunction(() => !document.querySelector('textarea').disabled);
        assert.equal(bodies.length, 2);
        assert.deepEqual(bodies[0], bodies[1]);
        assert.equal(chats, 0);
        const listing = await page.request.get(`${base}/api/workspaces/${a.external_id}/tasks`);
        const items = (await listing.json()).items;
        assert.equal(items.length, 1);
        assert.equal(new URL(page.url()).searchParams.get('task'), items[0].external_id);
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('隔离模型：认证聊天成功。', { exact: true }).waitFor();
        assert.equal(chats, 1);
        assert.equal(bodies.length, 2);
        await page.setViewportSize({ width: 1920, height: 1080 });
        await page.emulateMedia({ colorScheme: 'dark' });
        await page.screenshot({ path: `${artifacts}/creation-recovered-1920.png` });
    });
    for (const code of ['task_creation_conflict', 'task_creation_result_deleted']) {
        await scenario(`${code} blocks retry until explicit new intent`, async page => {
            const name = code === 'task_creation_conflict' ? '冲突项目' : '删除项目';
            const workspace = await project(page, name);
            const bodies = [];
            await page.route(`**/api/workspaces/${workspace.external_id}/tasks`, async route => {
                if (route.request().method() !== 'POST') return route.continue();
                bodies.push(route.request().postDataJSON());
                await route.fulfill({ status: 409, json: { code, message: '<script>PRIVATE</script>' } });
            });
            await page.goto(base);
            await draft(page, name);
            await submit(page, '第一份创建');
            await page.getByRole('button', { name: '放弃本次重试，开始另一任务', exact: true }).waitFor();
            assert.equal(await page.getByRole('button', { name: '重试创建', exact: true }).count(), 0);
            assert.equal(await page.getByText('<script>PRIVATE</script>', { exact: true }).count(), 0);
            await page.getByRole('button', { name: '放弃本次重试，开始另一任务', exact: true }).click();
            await submit(page, '第二份创建');
            await page.getByRole('button', { name: '放弃本次重试，开始另一任务', exact: true }).waitFor();
            assert.equal(bodies.length, 2);
            assert.notEqual(bodies[0].request_key, bodies[1].request_key);
            assert.equal(bodies[1].title, '第二份创建');
        });
    }
    await scenario('repeated uncertain response retains key and never sends messages', async page => {
        const workspace = await project(page, '重复超时项目');
        const bodies = [];
        let chats = 0;
        page.on('request', req => { if (req.url().endsWith('/api/chat/stream')) chats++; });
        await page.route(`**/api/workspaces/${workspace.external_id}/tasks`, async route => {
            if (route.request().method() !== 'POST') return route.continue();
            bodies.push(route.request().postDataJSON());
            await route.fulfill({ status: 504, json: { code: 'task_creation_uncertain', message: 'PRIVATE' } });
        });
        await page.goto(base);
        await draft(page, '重复超时项目');
        await submit(page, '固定输入');
        await page.getByRole('button', { name: '重试创建', exact: true }).click();
        await page.getByText('创建结果未确认或请求被拒绝。可以重试同一次创建，不会自动发送消息。', { exact: true }).waitFor();
        assert.equal(bodies.length, 2);
        assert.deepEqual(bodies[0], bodies[1]);
        assert.equal(chats, 0);
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
    });
    console.log(`Creation UI: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }

// 可在同一轮隔离服务中补跑既有首发、切换与历史隔离回归。
if (process.env.CREATION_REGRESSION === "1") await import("./workspace-task.mjs");
