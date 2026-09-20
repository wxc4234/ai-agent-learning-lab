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
    if (process.env.BROWSER_SCENARIO && !process.env.BROWSER_SCENARIO.split(',').some(prefix => name.startsWith(prefix))) return;
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (/same key|unique.*key|hydration/i.test(message.text())) errors.push(message.text()); });
    try { await run(page); assert.deepEqual(errors, []); passed++; console.log(`PASS ${name}`); }
    catch (error) { failures.push(name); console.error(`FAIL ${name}: ${error.stack}`); await page.screenshot({ path: `${artifacts}/task-fail-${failures.length}.png`, timeout: 10000 }).catch(() => {}); }
    finally { await context.close(); }
}
async function project(page, name) {
    const res = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(res.status(), 201);
    return res.json();
}
async function openDraft(page, name) {
    await page.getByRole('button', { name: `在 ${name} 新建任务`, exact: true }).click();
    await page.getByLabel('你的问题').waitFor();
}
const tasks = (page, name) => page.getByRole('list', { name: `${name} 任务`, exact: true });
async function send(page, prompt) {
    await page.getByLabel('你的问题').fill(prompt);
    await page.getByLabel('你的问题').press('Enter');
    await page.getByRole('status', { includeHidden: true }).filter({ hasText: '已完成' }).waitFor({ state: 'attached' });
}
try {
    await scenario('first send creates once, summarizes, continues same conversation and restores history', async page => {
        const workspace = await project(page, '对话交互项目');
        let creations = 0;
        const sessionIds = [];
        await page.route('**/api/workspaces/*/tasks', async route => { if (route.request().method() === 'POST') creations++; await route.continue(); });
        page.on('request', request => { if (request.url().endsWith('/api/chat/stream')) sessionIds.push(request.postDataJSON().session_id); });
        await page.goto(base);
        await openDraft(page, '对话交互项目');
        assert.equal(await page.getByLabel('任务标题', { exact: true }).count(), 0);
        assert.equal(await page.getByText('项目标识', { exact: true }).count(), 0);
        assert.equal(creations, 0);
        assert.equal(await page.getByRole('button', { name: '对话交互项目', exact: true }).evaluate(n => getComputedStyle(n).fontSize), '18px');
        assert.equal(await page.getByRole('button', { name: '展开详情', exact: true }).count(), 1);
        for (const [width, height] of [[1366, 768], [1920, 1080], [2560, 1318]]) {
            await page.setViewportSize({ width, height });
            const title = await page.getByRole('heading', { name: '今天想完成什么？' }).boundingBox();
            const composer = await page.getByLabel('你的问题').boundingBox();
            assert.ok(composer.y - title.y < 160);
            assert.ok(composer.y > height * 0.25 && composer.y < height * 0.7);
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${artifacts}/task-empty-${width}.png`, timeout: 10000, animations: 'disabled' });
        }
        await page.setViewportSize({ width: 1366, height: 768 });
        await page.getByLabel('你的问题').fill('输入法确认');
        await page.getByLabel('你的问题').dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, bubbles: true });
        assert.equal(creations, 0);
        await page.getByLabel('你的问题').press('Shift+Enter');
        assert.ok((await page.getByLabel('你的问题').inputValue()).includes('\n'));
        await page.getByLabel('你的问题').fill('帮我实现搜索功能');
        // 同一个事件循环双提交只能创建一次、发送一次。
        await page.getByLabel('你的问题').evaluate(n => { n.form.requestSubmit(); n.form.requestSubmit(); });
        await tasks(page, '对话交互项目').getByRole('button', { name: '自动总结的任务标题', exact: true }).waitFor();
        assert.equal(creations, 1);
        assert.equal(sessionIds.length, 1);
        await send(page, '接着增加测试');
        assert.equal(creations, 1);
        assert.equal(sessionIds.length, 2);
        assert.equal(sessionIds[0], sessionIds[1]);
        const list = await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks`);
        const task = (await list.json()).items[0];
        assert.equal(task.title, '自动总结的任务标题');
        assert.equal(task.conversation_id, sessionIds[0]);
        const link = page.url();
        assert.equal(new URL(link).searchParams.get('workspace'), workspace.external_id);
        assert.equal(new URL(link).searchParams.get('task'), task.external_id);
        await page.reload();
        // 刷新后必须自动恢复，不能通过手动点击掩盖恢复失败。
        await page.getByText('接着增加测试', { exact: true }).waitFor();
        assert.equal(await page.getByText('帮我实现搜索功能', { exact: true }).count(), 1);
        const copied = await page.context().newPage();
        await copied.goto(link);
        await copied.getByText('接着增加测试', { exact: true }).waitFor();
        await copied.close();
        await send(page, '刷新后继续');
        assert.equal(sessionIds.length, 3);
        assert.equal(sessionIds[2], task.conversation_id);
        assert.equal(creations, 1);
        await page.getByLabel('你的问题').fill('未发送的草稿');
        await page.getByRole('button', { name: '收起导航', exact: true }).click();
        await page.getByRole('button', { name: '展开导航', exact: true }).click();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '未发送的草稿');
        for (const [width, height] of [[1366, 768], [1920, 1080]]) {
            await page.setViewportSize({ width, height });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1));
            await page.screenshot({ path: `${artifacts}/task-conversation-${width}.png`, timeout: 10000, animations: 'disabled' });
        }
    });
    await scenario('new draft is empty, directory is in settings and project switching is isolated', async page => {
        await project(page, '项目甲'); await project(page, '项目乙');
        await page.goto(base);
        await openDraft(page, '项目甲');
        await send(page, '甲项目的内容');
        await tasks(page, '项目甲').getByRole('button', { name: '自动总结的任务标题', exact: true }).waitFor();
        await openDraft(page, '项目乙');
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        assert.equal(new URL(page.url()).searchParams.has('workspace'), false);
        assert.equal(await page.getByText('甲项目的内容', { exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '选择项目目录', exact: true }).count(), 0);
        await page.getByRole('button', { name: '项目乙 项目操作', exact: true }).click();
        await page.getByRole('menuitem', { name: '项目设置', exact: true }).click();
        await page.getByRole('button', { name: '选择项目目录', exact: true }).waitFor();
        await page.getByRole('button', { name: '关闭项目设置', exact: true }).click();
        await page.getByLabel('你的问题').fill('尚未发送');
        await page.getByRole('button', { name: '新对话', exact: true }).click();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '');
    });
    await scenario('committed creation with lost response blocks send and can be opened from list', async page => {
        await project(page, '丢包恢复项目');
        let writes = 0; let chats = 0;
        page.on('request', r => { if (r.url().endsWith('/api/chat/stream')) chats++; });
        await page.route('**/api/workspaces/*/tasks', async route => {
            if (route.request().method() !== 'POST') return route.continue();
            writes++; const response = await route.fetch(); assert.equal(response.status(), 201); await route.abort('failed');
        });
        await page.goto(base); await openDraft(page, '丢包恢复项目');
        await page.getByLabel('你的问题').fill('丢包但已创建');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('创建结果未确认或请求被拒绝。可以重试同一次创建，不会自动发送消息。', { exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: '发送', exact: true }).isDisabled(), true);
        assert.equal(writes, 1); assert.equal(chats, 0);
        await tasks(page, '丢包恢复项目').getByRole('button', { name: '丢包但已创建', exact: true }).click();
        await page.getByText('今天想完成什么？', { exact: true }).waitFor();
        await page.unroute('**/api/workspaces/*/tasks');
        await send(page, '丢包但已创建');
        assert.equal(chats, 1); assert.equal(writes, 1);
    });
    await scenario('history failure blocks sending until retry; stale history cannot enter new task', async page => {
        const workspace = await project(page, '历史异常项目');
        const res = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '已有任务' } });
        assert.equal(res.status(), 201);
        await page.route('**/api/workspaces/*/tasks/*/messages', route => route.fulfill({ status: 502, contentType: 'application/json', body: '{}' }));
        await page.goto(base);
        await tasks(page, '历史异常项目').getByRole('button', { name: '已有任务', exact: true }).click();
        await page.getByText('历史读取失败。', { exact: false }).waitFor();
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
        await page.unroute('**/api/workspaces/*/tasks/*/messages');
        await page.getByRole('button', { name: '重新读取', exact: true }).click();
        await page.getByText('今天想完成什么？', { exact: true }).waitFor();
        await openDraft(page, '历史异常项目');
        assert.equal(await page.getByText('历史读取失败。', { exact: false }).count(), 0);
        // 即使旧 fetch 忽略 abort，迟到历史也不能渲染进新草稿。
        await page.evaluate(() => {
            const original = window.fetch;
            window.fetch = (url, init) => String(url).endsWith('/messages')
                ? new Promise(resolve => { window.releaseHistory = () => resolve(new Response(JSON.stringify({ messages: [{ role: 'assistant', content: '旧会话污染' }] }))); })
                : original(url, init);
        });
        await tasks(page, '历史异常项目').getByRole('button', { name: '已有任务', exact: true }).click();
        await page.getByRole('status', { name: '正在读取对话', exact: true }).waitFor();
        await openDraft(page, '历史异常项目');
        await page.evaluate(() => window.releaseHistory());
        assert.equal(await page.getByText('旧会话污染', { exact: true }).count(), 0);
    });
    await scenario('URL invalid identifiers and missing task never become a draft', async page => {
        const workspace = await project(page, '链接错误项目');
        let creations = 0;
        page.on('request', request => {
            if (request.method() === 'POST' && /\/tasks$/.test(request.url())) creations++;
        });
        await page.goto(`${base}/?task=bad`);
        await page.getByText('任务链接不完整或格式不正确，请从左侧重新选择任务。').waitFor();
        assert.equal(await page.getByLabel('你的问题').count(), 0);
        await page.goto(`${base}/?workspace=${workspace.external_id}&task=${'f'.repeat(32)}`);
        await page.getByText('任务恢复失败，任务可能不可访问或本地服务暂时不可用。').waitFor();
        assert.equal(await page.getByLabel('你的问题').count(), 0);
        await openDraft(page, '链接错误项目');
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        assert.equal(creations, 0);
    });
    await scenario('URL detail failure retries and history failure still blocks send', async page => {
        const workspace = await project(page, '恢复重试项目');
        const response = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '恢复重试任务' } });
        assert.equal(response.status(), 201);
        const task = await response.json();
        const detailUrl = `${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}`;
        await page.route(detailUrl, route => route.fulfill({ status: 502, body: '{}' }));
        await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
        await page.getByRole('button', { name: '重新读取任务', exact: true }).waitFor();
        assert.equal(await page.getByLabel('你的问题').count(), 0);
        await page.unroute(detailUrl);
        await page.route(`${detailUrl}/messages`, route => route.fulfill({ status: 502, body: '{}' }));
        await page.getByRole('button', { name: '重新读取任务', exact: true }).click();
        await page.getByText('历史读取失败。', { exact: false }).waitFor();
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
        await page.unroute(`${detailUrl}/messages`);
        await page.getByRole('button', { name: '重新读取', exact: true }).click();
        await page.getByText('今天想完成什么？', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('你的问题').isEnabled(), true);
    });
    await scenario('URL restores outside project and task first pages', async page => {
        const workspace = await project(page, '分页外恢复项目');
        const response = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '分页外恢复任务' } });
        assert.equal(response.status(), 201);
        const task = await response.json();
        for (let i = 0; i < 21; i++) {
            const newer = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: `较新任务${i}` } });
            assert.equal(newer.status(), 201);
            await project(page, `较新项目${i}`);
        }
        const list = await (await page.request.get(`${base}/api/workspaces?limit=20`)).json();
        const taskList = await (await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks`)).json();
        assert.ok(!list.items.some(item => item.external_id === workspace.external_id));
        assert.ok(!taskList.items.some(item => item.external_id === task.external_id));
        await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
        await page.getByRole('heading', { name: task.title, exact: true }).waitFor();
        await tasks(page, workspace.name).getByRole('button', { name: task.title, exact: true }).waitFor();
        await page.getByText('今天想完成什么？', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('你的问题').isEnabled(), true);
        await page.route('**/api/workspaces?limit=20', route => route.fulfill({ status: 502, body: '{}' }));
        await page.reload();
        await page.getByRole('button', { name: '项目读取失败，重试', exact: true }).waitFor();
        await page.getByRole('heading', { name: task.title, exact: true }).waitFor();
        await page.getByText('今天想完成什么？', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('你的问题').isEnabled(), true);
        await page.screenshot({ path: `${artifacts}/task-url-restored.png`, animations: 'disabled' });
    });
    await scenario('URL stale detail ignoring abort cannot overwrite a new selection', async page => {
        const workspace = await project(page, '恢复竞态项目');
        const response = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '迟到任务' } });
        assert.equal(response.status(), 201);
        const task = await response.json();
        await page.addInitScript(({ workspace, task }) => {
            const original = window.fetch;
            const releases = [];
            window.releaseDetails = () => releases.splice(0).forEach(resolve => resolve(new Response(JSON.stringify({ workspace, task }))));
            window.fetch = (url, init) => String(url).endsWith(`/tasks/${task.external_id}`)
                ? new Promise(resolve => releases.push(resolve))
                : original(url, init);
        }, { workspace, task });
        await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
        await page.getByText('正在恢复任务…', { exact: true }).waitFor();
        await openDraft(page, workspace.name);
        await page.getByLabel('你的问题').fill('新草稿不能被覆盖');
        await page.evaluate(async () => {
            window.releaseDetails();
            await new Promise(resolve => setTimeout(resolve, 0));
        });
        assert.equal(await page.getByRole('heading', { name: '新对话', exact: true }).count(), 1);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '新草稿不能被覆盖');
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
    });
    await scenario('delete BFF empty task, repeat and history guard', async page => {
        const workspace = await project(page, '删除代理验收');
        const collection = `/api/workspaces/${workspace.external_id}/tasks`;
        const created = await page.request.post(`${base}${collection}`, { headers: { Origin: base }, data: { title: '可删除空任务' } });
        assert.equal(created.status(), 201);
        const empty = await created.json();
        await page.goto(base);
        // 在浏览器发出同源 DELETE，由浏览器附加真实 Origin。
        const remove = id => page.evaluate(async path => {
            const response = await fetch(path, { method: 'DELETE' });
            return { status: response.status, body: await response.text(), cache: response.headers.get('cache-control') };
        }, `${collection}/${id}`);
        assert.deepEqual(await remove(empty.external_id), { status: 204, body: '', cache: 'no-store' });
        const repeated = await remove(empty.external_id);
        assert.equal(repeated.status, 404);
        assert.equal(JSON.parse(repeated.body).code, 'workspace_not_accessible');
        assert.equal((await page.request.get(`${base}${collection}/${empty.external_id}`)).status(), 404);
        assert.equal((await (await page.request.get(`${base}${collection}`)).json()).items.length, 0);
        await page.reload();
        await openDraft(page, workspace.name);
        await send(page, '保留已有历史');
        const used = (await (await page.request.get(`${base}${collection}`)).json()).items[0];
        for (let attempt = 0; attempt < 40; attempt++) {
            const response = await page.request.get(`${base}/api/sessions/${used.conversation_id}/execution`);
            if (!(await response.json()).occupied) break;
            await new Promise(resolve => setTimeout(resolve, 150));
        }
        assert.equal((await remove(used.external_id)).status, 204);
        assert.equal((await page.request.get(`${base}${collection}/${used.external_id}`)).status(), 404);
    });
    console.log(`Task conversation: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }
