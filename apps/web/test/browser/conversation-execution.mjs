import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
const page = await context.newPage();
const owner = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.setDefaultTimeout(30000);
owner.setDefaultTimeout(30000);
const panel = () => page.getByRole('region', { name: '会话执行占用', exact: true });
const idleText = '查询时未发现执行占用';
const busyText = '查询时存在执行占用';
const unknown = '查询失败，当前占用状态未知，请重试。';
let chatPosts = 0;
page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/api/chat/stream')) chatPosts++; });
try {
    const response = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name: '占用状态UI验收' } });
    assert.equal(response.status(), 201);
    const workspace = await response.json();
    const tasks = [];
    for (const title of ['占用查询甲', '占用查询乙']) {
        const created = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title } });
        assert.equal(created.status(), 201);
        tasks.push(await created.json());
    }
    const [a, b] = tasks;
    const url = `${base}/?workspace=${workspace.external_id}&task=${a.external_id}`;
    for (const current of [owner, page]) {
        await current.bringToFront();
        const loaded = current.waitForResponse(r => r.url().endsWith(`/tasks/${a.external_id}/messages`) && r.status() === 200);
        await current.goto(url);
        await loaded;
        await current.waitForFunction(() => { const input = document.querySelector('textarea'); return input && !input.disabled; });
    }
    if (await page.getByRole('button', { name: '展开详情', exact: true }).isVisible()) await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await panel().getByText('尚未查询，可手动查看会话占用。', { exact: true }).waitFor();
    const queryButton = panel().getByRole('button', { name: '查询状态', exact: true });
    await queryButton.focus();
    await page.keyboard.press('Enter');
    await panel().getByText(idleText, { exact: true }).waitFor();
    assert.equal(await panel().locator('time').count(), 1);
    await owner.bringToFront();
    await owner.getByLabel('你的问题').fill('[cancel-test] 占用状态只读验收');
    const started = owner.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
    await owner.getByLabel('你的问题').press('Enter');
    assert.equal((await started).status(), 200);
    await page.bringToFront();
    await panel().getByRole('button', { name: '刷新状态', exact: true }).click();
    await panel().getByText(busyText, { exact: true }).waitFor();
    assert.equal(await panel().locator('time').count(), 2);
    // 连续读取仍被占用，证明查询不会释放；观察页也没有发送聊天。
    const stillBusy = await page.request.get(`${base}/api/sessions/${a.conversation_id}/execution`);
    assert.equal((await stillBusy.json()).occupied, true);
    assert.equal(chatPosts, 0);
    assert.equal(await page.getByLabel('你的问题').isEnabled(), true);
    await page.screenshot({ path: `${output}/execution-query-busy-1366.png` });
    console.log('PASS real API/BFF/UI idle and busy snapshots, keyboard query, no chat side effects');
    await owner.bringToFront();
    const cancelled = owner.waitForResponse(r => /\/api\/runs\/\d+\/cancel$/.test(r.url()));
    await owner.getByRole('button', { name: '停止生成', exact: true }).click();
    assert.equal((await cancelled).status(), 204);
    let released = false;
    for (let i = 0; i < 30; i++) {
        const check = await page.request.get(`${base}/api/sessions/${a.conversation_id}/execution`);
        if (!(await check.json()).occupied) { released = true; break; }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(released);
    await page.bringToFront();
    await panel().getByRole('button', { name: '刷新状态', exact: true }).click();
    await panel().getByText(idleText, { exact: true }).waitFor();
    console.log('PASS cancellation releases real slot; manual UI refresh sees idle');

    // 模拟上游失败/迟到结果；故意忽略 abort，验证组件自身也会拒绝旧回调。
    await page.evaluate(() => {
        const original = window.fetch.bind(window);
        window.executionTest = { mode: 'real', pending: [], count: 0, original };
        window.fetch = (input, init) => {
            const state = window.executionTest;
            if (!String(input).includes('/api/sessions/') || !String(input).endsWith('/execution')) return original(input, init);
            state.count++;
            if (state.mode === 'error') return Promise.resolve(Response.json({ message: 'PRIVATE' }, { status: 502 }));
            if (state.mode === 'invalid') return Promise.resolve(Response.json({ session_id: 'wrong', occupied: false, acquired_at: null }));
            if (state.mode === 'hold') return new Promise(resolve => state.pending.push({ resolve, signal: init.signal }));
            return original(input, init);
        };
    });
    for (const mode of ['error', 'invalid']) {
        await page.evaluate(mode => { window.executionTest.mode = mode; }, mode);
        await panel().getByRole('button', { name: '刷新状态', exact: true }).click();
        await panel().getByText(unknown, { exact: true }).waitFor();
        assert.equal(await panel().getByText(idleText, { exact: true }).count(), 0);
        await page.evaluate(() => { window.executionTest.mode = 'real'; });
        await panel().getByRole('button', { name: '重试查询', exact: true }).click();
        await panel().getByText(idleText, { exact: true }).waitFor();
    }
    console.log('PASS HTTP/contract errors show unknown and manual retry recovers');
    await page.evaluate(() => { window.executionTest.mode = 'hold'; });
    await panel().getByRole('button', { name: '刷新状态', exact: true }).click();
    await panel().getByRole('button', { name: '查询中…', exact: true }).waitFor();
    assert.equal(await panel().getByRole('button', { name: '查询中…', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: b.title, exact: true }).click();
    await panel().getByText('尚未查询，可手动查看会话占用。', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.executionTest.pending[0].signal.aborted), true);
    await page.evaluate(() => { window.executionTest.mode = 'real'; });
    await panel().getByRole('button', { name: '查询状态', exact: true }).click();
    await panel().getByText(idleText, { exact: true }).waitFor();
    await page.evaluate(sessionId => {
        window.executionTest.pending.shift().resolve(Response.json({ session_id: sessionId, occupied: true, acquired_at: '2000-01-01T00:00:00Z' }));
    }, a.conversation_id);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await panel().getByText(busyText, { exact: true }).count(), 0);
    await panel().getByText(idleText, { exact: true }).waitFor();
    console.log('PASS task switch aborts old query and rejects deliberately late result');
    await page.evaluate(() => { window.executionTest.mode = 'hold'; });
    await panel().getByRole('button', { name: '刷新状态', exact: true }).click();
    await panel().getByRole('button', { name: '查询中…', exact: true }).waitFor();
    await page.getByRole('button', { name: '收起详情', exact: true }).click();
    assert.equal(await page.evaluate(() => window.executionTest.pending[0].signal.aborted), true);
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await panel().getByText('尚未查询，可手动查看会话占用。', { exact: true }).waitFor();
    await page.evaluate(sessionId => {
        window.executionTest.pending.shift().resolve(Response.json({ session_id: sessionId, occupied: true, acquired_at: '2000-01-01T00:00:00Z' }));
        window.fetch = window.executionTest.original;
    }, b.conversation_id);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await panel().getByText('尚未查询，可手动查看会话占用。', { exact: true }).waitFor();
    await page.setViewportSize({ width: 1920, height: 900 });
    await panel().getByRole('button', { name: '查询状态', exact: true }).click();
    await panel().getByText(idleText, { exact: true }).waitFor();
    await page.screenshot({ path: `${output}/execution-query-idle-1920.png` });
    assert.equal(chatPosts, 0);
    assert.deepEqual(errors, []);
    console.log('PASS close/reopen resets query and ignores late result; desktop 1920 verified');
} catch (error) {
    await page.screenshot({ path: `${output}/execution-query-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
