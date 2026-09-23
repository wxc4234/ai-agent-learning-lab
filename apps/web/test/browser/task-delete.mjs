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
// 通过真实 BFF 创建空任务；仅网络故障场景拦截浏览器响应。
async function emptyTask(page, workspace, title) {
    const response = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, {
        headers: { Origin: base }, data: { title },
    });
    assert.equal(response.status(), 201);
    return response.json();
}
const taskPath = task => `/api/workspaces/${task.workspace_id}/tasks/${task.external_id}`;
const deletion = page => page.getByRole('region', { name: '任务删除状态' });
const actionButton = (page, task) => page.getByRole('button', { name: `删除任务：${task.title}`, exact: true });
async function deletionDisabled(page, task) {
    return actionButton(page, task).isDisabled();
}

async function openTask(page, task) {
    await page.goto(`${base}/?workspace=${task.workspace_id}&task=${task.external_id}`);
    await actionButton(page, task).waitFor();
    await page.getByLabel('你的问题').waitFor();
}
async function remove(page, task, accept = true) {
    await actionButton(page, task).click();
    const dialog = page.getByRole('alertdialog');
    await dialog.waitFor();
    assert.ok((await dialog.innerText()).includes(task.title));
    await dialog.getByRole('button', { name: accept ? '删除任务' : '取消', exact: true }).click();
}

async function absent(page, task) {
    assert.equal((await page.request.get(`${base}${taskPath(task)}`)).status(), 404);
}
try {
    await scenario('delete UI proposal application conflict', async page => {
        const workspace = await project(page, '文件应用占用保护');
        const task = await emptyTask(page, workspace, '保留应用中的任务');
        await openTask(page, task);
        await page.getByLabel('你的问题').fill('保留草稿');
        let requests = 0;
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() !== 'DELETE') return route.continue();
            requests++;
            return route.fulfill({ status: 409, json: { code: 'proposal_application_busy', message: 'PRIVATE' } });
        });
        await remove(page, task);
        await deletion(page).getByText('存在执行中或结果未确认的文件应用，暂不能删除任务。请先核对应用结果。', { exact: true }).waitFor();
        assert.equal(requests, 1);
        assert.equal(await deletionDisabled(page, task), false);
        assert.equal(new URL(page.url()).searchParams.get('task'), task.external_id);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '保留草稿');
        assert.ok(!(await deletion(page).innerText()).includes('PRIVATE'));
        assert.equal((await page.request.get(`${base}${taskPath(task)}`)).status(), 200);
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${artifacts}/proposal-application-busy-${width}.png` });
        }
    });
    await scenario('delete UI sample-bound conflict', async page => {
        const workspace = await project(page, '受限样例删除保护');
        const task = await emptyTask(page, workspace, '保留样例来源任务');
        await openTask(page, task);
        await page.getByLabel('你的问题').fill('保留未发送草稿');
        let requests = 0;
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() !== 'DELETE') return route.continue();
            requests++;
            return route.fulfill({ status: 409, json: { code: 'task_sample_bound', message: 'PRIVATE' } });
        });
        await remove(page, task);
        await deletion(page).getByText('该任务仍绑定受限样例，暂不能删除。请先核对样例状态。', { exact: true }).waitFor();
        assert.equal(requests, 1);
        assert.equal(await deletionDisabled(page, task), false);
        assert.equal(new URL(page.url()).searchParams.get('task'), task.external_id);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '保留未发送草稿');
        assert.ok(!(await deletion(page).innerText()).includes('PRIVATE'));
        assert.equal((await page.request.get(`${base}${taskPath(task)}`)).status(), 200);
    });
    await scenario('delete UI execution conflict preserves task and allows manual retry', async page => {
        const workspace = await project(page, '执行占用删除验收');
        const task = await emptyTask(page, workspace, '保留执行中的任务');
        await openTask(page, task);
        const runner = await page.context().newPage();
        runner.setDefaultTimeout(30000);
        await runner.bringToFront();
        await openTask(runner, task);
        await runner.waitForFunction(() => {
            const input = document.querySelector('textarea');
            return input && !input.disabled;
        });
        const started = runner.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await runner.getByLabel('你的问题').fill('[cancel-held] 保持执行占用');
        await runner.getByLabel('你的问题').press('Enter');
        assert.equal((await started).status(), 200);
        console.log('READY execution started');
        await page.bringToFront();
        await page.getByRole('button', { name: '展开详情', exact: true }).click();
        const execution = page.getByRole('region', { name: '会话执行占用' });
        await execution.getByRole('button', { name: '查询状态', exact: true }).click();
        await execution.getByText('查询时存在执行占用', { exact: true }).waitFor();
        const recoveryRefused = page.waitForResponse(r => r.url().endsWith('/execution/recover'));
        await execution.getByRole('button', { name: '检查并恢复异常运行', exact: true }).click();
        assert.equal((await recoveryRefused).status(), 409);
        await execution.getByText('无法确认原执行进程已退出，未解除占用。进程存活、身份未知或非本机执行时均会拒绝。', { exact: true }).waitFor();
        await page.getByRole('button', { name: '收起详情', exact: true }).click();
        await page.getByLabel('你的问题').fill('删除拒绝后保留草稿');
        let requests = 0;
        page.on('request', request => { if (request.method() === 'DELETE') requests++; });
        const rejected = page.waitForResponse(r => r.request().method() === 'DELETE');
        await remove(page, task);
        console.log('READY delete clicked');
        const response = await rejected;
        assert.equal(response.status(), 409);
        assert.equal((await response.json()).code, 'conversation_busy');
        assert.equal(response.headers()['cache-control'], 'no-store');
        await deletion(page).getByText('该任务仍有执行占用，暂不能删除。请等待执行及收尾完成后重试。', { exact: true }).waitFor();
        assert.equal(await deletionDisabled(page, task), false);
        assert.equal(new URL(page.url()).searchParams.get('task'), task.external_id);
        assert.equal(await page.getByLabel('你的问题').inputValue(), '删除拒绝后保留草稿');
        assert.equal((await page.request.get(`${base}${taskPath(task)}`)).status(), 200);
        await page.screenshot({ path: `${artifacts}/delete-busy-1366.png`, animations: 'disabled' });
        await page.setViewportSize({ width: 1920, height: 1080 });
        await page.screenshot({ path: `${artifacts}/delete-busy-1920.png`, animations: 'disabled' });
        assert.equal(requests, 1);

        const cancelled = runner.waitForResponse(r => /\/api\/runs\/\d+\/cancel$/.test(r.url()));
        await runner.getByRole('button', { name: '停止生成', exact: true }).click();
        assert.equal((await cancelled).status(), 204);
        // 轮询只读占用，不把取消响应直接当成执行已停止。
        let released = false;
        for (let attempt = 0; attempt < 40; attempt++) {
            const current = await page.request.get(`${base}/api/sessions/${task.conversation_id}/execution`);
            assert.equal(current.status(), 200);
            if (!(await current.json()).occupied) { released = true; break; }
            await new Promise(resolve => setTimeout(resolve, 150));
        }
        assert.ok(released);
        await remove(page, task);
        await deletion(page).getByText('任务已删除。', { exact: true }).waitFor();
        assert.equal(requests, 2);
        await absent(page, task);
    });
    await scenario('delete UI confirm, cancel, current task and keyboard', async page => {
        const workspace = await project(page, '删除界面基本流程');
        const task = await emptyTask(page, workspace, '待删除空任务');
        await openTask(page, task);
        await page.getByRole('button', { name: '新对话', exact: true }).hover();
        await actionButton(page, task).evaluate(el => el.blur());
        // transition 完成后再读取透明度，避免采到动画中间帧。
        await page.waitForFunction(label => getComputedStyle(document.querySelector(`[aria-label="${label}"]`)).opacity === '0', `删除任务：${task.title}`);
        await actionButton(page, task).hover();
        await page.screenshot({ path: `${artifacts}/delete-ui-hover.png`, animations: 'disabled' });
        await actionButton(page, task).click();
        await page.getByRole('alertdialog').waitFor();
        await page.screenshot({ path: `${artifacts}/delete-ui-confirm.png`, animations: 'disabled' });
        await page.keyboard.press('Escape');
        await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
        assert.equal(await actionButton(page, task).evaluate(el => el === document.activeElement), true);
        let requests = 0;
        page.on('request', req => { if (req.method() === 'DELETE') requests++; });
        await remove(page, task, false);
        assert.equal(requests, 0);
        assert.equal(new URL(page.url()).searchParams.get('task'), task.external_id);
        await actionButton(page, task).focus();
        await page.keyboard.press('Enter');
        const dialog = page.getByRole('alertdialog');
        await dialog.waitFor();
        assert.equal(await dialog.getByRole('button', { name: '取消', exact: true }).evaluate(el => el === document.activeElement), true);
        await dialog.getByRole('button', { name: '删除任务', exact: true }).focus();
        await page.keyboard.press('Enter');
        await deletion(page).getByText('任务已删除。', { exact: true }).waitFor();
        assert.equal(requests, 1);
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        assert.equal(new URL(page.url()).searchParams.has('workspace'), false);
        await page.getByRole('heading', { name: '新对话', exact: true }).waitFor();
        await tasks(page, workspace.name).getByText('点击笔形按钮开始任务').waitFor();
        await absent(page, task);
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${artifacts}/delete-ui-success-${width}.png`, animations: 'disabled' });
        }
    });

    await scenario('delete UI history deleted after execution ends', async page => {
        const workspace = await project(page, '删除历史保护');
        const task = await emptyTask(page, workspace, '历史保护任务');
        await openTask(page, task);
        await send(page, '保留这条历史消息');
        // 已有 Task 的首次发送不走草稿创建，不要求触发标题总结。
        const current = (await (await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks`)).json()).items[0];
        // 完成 UI 终态不等于服务已释放占用；测试显式等待只读快照。
        for (let attempt = 0; attempt < 40; attempt++) {
            const response = await page.request.get(`${base}/api/sessions/${task.conversation_id}/execution`);
            if (!(await response.json()).occupied) break;
            await new Promise(resolve => setTimeout(resolve, 150));
        }
        await remove(page, current);
        await deletion(page).getByText('任务已删除。', { exact: true }).waitFor();
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        await absent(page, task);
        await page.reload();
        assert.equal(await page.getByText('保留这条历史消息', { exact: true }).count(), 0);
    });

    for (const destination of ['task', 'draft']) {
        await scenario(`delete UI late success preserves newer ${destination}`, async page => {
            const workspace = await project(page, `迟到结果-${destination}`);
            const first = await emptyTask(page, workspace, `删除目标-${destination}`);
            const second = await emptyTask(page, workspace, `保留任务-${destination}`);
            await openTask(page, first);
            let release;
            const gate = new Promise(resolve => { release = resolve; });
            let requests = 0;
            await page.route(`**${taskPath(first)}`, async route => {
                if (route.request().method() !== 'DELETE') return route.continue();
                requests++;
                const response = await route.fetch();
                await gate;
                await route.fulfill({ response });
            });
            // 同一个事件循环点击两次：React 重绘之前仍应有 ref 锁。
            await actionButton(page, first).click();
            await page.getByRole('alertdialog').getByRole('button', { name: '删除任务', exact: true }).evaluate(button => { button.click(); button.click(); });
            await deletion(page).getByText('正在删除任务…', { exact: true }).waitFor();
            assert.equal(await deletionDisabled(page, second), true);
            if (destination === 'task') {
                await tasks(page, workspace.name).getByRole('button', { name: second.title, exact: true }).click();
            } else {
                await openDraft(page, workspace.name);
            }
            await page.getByLabel('你的问题').fill('新选择的未发送输入');
            // 刷新导航和侧栏隐藏都不能丢失 Provider 中的操作。
            await page.getByRole('button', { name: '刷新项目', exact: true }).click();
            await page.getByRole('button', { name: '收起导航', exact: true }).click();
            await page.getByRole('button', { name: '展开导航', exact: true }).click();
            await deletion(page).getByText('正在删除任务…', { exact: true }).waitFor();
            release();
            await deletion(page).getByText('任务已删除。', { exact: true }).waitFor();
            assert.equal(requests, 1);
            assert.equal(await page.getByLabel('你的问题').inputValue(), '新选择的未发送输入');
            assert.equal(new URL(page.url()).searchParams.get('task'), destination === 'task' ? second.external_id : null);
            await absent(page, first);
        });
    }

    await scenario('delete UI committed response lost then detail confirms absence', async page => {
        const workspace = await project(page, '响应丢失确认');
        const task = await emptyTask(page, workspace, '后端已删除');
        await openTask(page, task);
        let requests = 0;
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() !== 'DELETE') return route.continue();
            requests++;
            const response = await route.fetch();
            assert.equal(response.status(), 204);
            await route.abort('failed');
        });
        await remove(page, task);
        await deletion(page).getByText('删除结果未确认，请检查任务状态；不要重复提交删除。', { exact: true }).waitFor();
        assert.equal(await deletionDisabled(page, task), true);
        await page.getByRole('button', { name: '检查任务状态', exact: true }).click();
        await deletion(page).getByText('任务已不存在或不可访问，列表已刷新。', { exact: true }).waitFor();
        assert.equal(requests, 1);
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        await absent(page, task);
    });

    await scenario('delete UI uncertain checks existing, invalid, unavailable and absent', async page => {
        const workspace = await project(page, '结果查询边界');
        const task = await emptyTask(page, workspace, '保留待确认任务');
        await openTask(page, task);
        let checks = 0;
        let requests = 0;
        let mode = 'existing';
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() === 'DELETE') {
                requests++;
                return route.fulfill({ status: 504, json: { code: 'task_deletion_uncertain', message: 'PRIVATE' } });
            }
            checks++;
            if (mode === 'existing') return route.continue();
            if (mode === 'invalid') return route.fulfill({ status: 200, json: { task: { external_id: 'bad' } } });
            if (mode === 'unavailable') return route.fulfill({ status: 503, body: 'PRIVATE' });
            return route.fulfill({ status: 404, json: { message: '不可访问' } });
        });
        await remove(page, task);
        await page.getByRole('button', { name: '检查任务状态', exact: true }).click();
        await deletion(page).getByText('任务目前仍存在，先前删除结果尚未确认，请稍后再次检查。', { exact: true }).waitFor();
        assert.equal(await deletionDisabled(page, task), true);
        for (mode of ['invalid', 'unavailable']) {
            await page.getByRole('button', { name: '检查任务状态', exact: true }).click();
            await deletion(page).getByText('任务状态检查失败，请确认本地服务可用后再次检查。', { exact: true }).waitFor();
        }
        await page.screenshot({ path: `${artifacts}/delete-ui-uncertain.png`, animations: 'disabled' });
        assert.ok(!(await deletion(page).innerText()).includes('PRIVATE'));
        // 改选草稿后确认目标不可访问，也不能覆盖草稿输入。
        await openDraft(page, workspace.name);
        await page.getByLabel('你的问题').fill('查询时保留草稿');
        mode = 'absent';
        await page.getByRole('button', { name: '检查任务状态', exact: true }).click();
        await deletion(page).getByText('任务已不存在或不可访问，列表已刷新。', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '查询时保留草稿');
        assert.equal(requests, 1);
        assert.equal(checks, 4);
    });

    await scenario('delete UI known rejection versus unknown status code', async page => {
        const workspace = await project(page, '删除错误映射');
        const task = await emptyTask(page, workspace, '错误映射任务');
        await openTask(page, task);
        let status = 403;
        let code = 'local_access_rejected';
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() !== 'DELETE') return route.continue();
            return route.fulfill({ status, json: { code, message: 'PRIVATE' } });
        });
        for ([status, code] of [[403, 'local_access_rejected'], [422, 'invalid_task_input'], [400, 'invalid_task_request'], [499, 'task_request_cancelled']]) {
            await remove(page, task);
            await deletion(page).getByText('删除请求被拒绝或尚未提交，请检查本地服务后重试。', { exact: true }).waitFor();
            assert.equal(!await deletionDisabled(page, task), true);
            assert.equal(new URL(page.url()).searchParams.get('task'), task.external_id);
        }
        status = 409;
        code = 'unknown';
        await remove(page, task);
        await deletion(page).getByText('删除结果未确认，请检查任务状态；不要重复提交删除。', { exact: true }).waitFor();
        assert.equal(await deletionDisabled(page, task), true);
        assert.ok(!(await deletion(page).innerText()).includes('PRIVATE'));
    });
    await scenario('delete UI cancels late URL restoration of removed task', async page => {
        const workspace = await project(page, '恢复与删除竞争');
        const task = await emptyTask(page, workspace, '正在恢复的任务');
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        await page.route(`**${taskPath(task)}`, async route => {
            if (route.request().method() !== 'GET') return route.continue();
            const response = await route.fetch();
            await gate;
            // 浏览器主动取消后，路由交付可能报已关闭，这是本场景预期。
            await route.fulfill({ response }).catch(() => {});
        });
        await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
        await page.getByText('正在恢复任务…', { exact: true }).waitFor();
        await page.getByRole('button', { name: workspace.name, exact: true }).click();
        await remove(page, task);
        await deletion(page).getByText('任务已删除。', { exact: true }).waitFor();
        release();
        await page.getByRole('heading', { name: '新对话', exact: true }).waitFor();
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
        await page.getByLabel('你的问题').fill('删除后的草稿');
        assert.equal(await page.getByLabel('你的问题').inputValue(), '删除后的草稿');
        await absent(page, task);
    });
    await scenario('project menu appearance, keyboard and wired actions', async page => {
        const workspace = await project(page, '项目菜单验收');
        const task = await emptyTask(page, workspace, '项目菜单测试任务');
        await openTask(page, task);
        const trigger = page.getByRole('button', { name: `${workspace.name} 项目操作`, exact: true });
        await trigger.focus();
        await page.keyboard.press('Enter');
        const menu = page.getByRole('menu');
        await menu.waitFor();
        assert.deepEqual((await menu.getByRole('menuitem').allTextContents()).map(text => text.trim()), ['新建任务', '项目设置', '刷新任务']);
        await page.screenshot({ path: `${artifacts}/project-actions-menu.png`, animations: 'disabled' });
        await page.setViewportSize({ width: 1920, height: 1080 });
        await page.emulateMedia({ colorScheme: 'dark' });
        await page.screenshot({ path: `${artifacts}/project-actions-menu-dark.png`, animations: 'disabled' });
        await page.emulateMedia({ colorScheme: 'light' });
        await page.setViewportSize({ width: 1366, height: 768 });
        await page.keyboard.press('Escape');
        await menu.waitFor({ state: 'hidden' });
        assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
        await trigger.click();
        await page.getByRole('menuitem', { name: '项目设置', exact: true }).click();
        await page.getByRole('region', { name: `${workspace.name} 设置`, exact: true }).waitFor();
        await page.getByRole('button', { name: '关闭项目设置', exact: true }).click();
        await trigger.click();
        const refreshed = page.waitForResponse(response => response.url().includes(`/workspaces/${workspace.external_id}/tasks`) && response.request().method() === 'GET');
        await page.getByRole('menuitem', { name: '刷新任务', exact: true }).click();
        assert.equal((await refreshed).status(), 200);
        await trigger.click();
        await page.getByRole('menuitem', { name: '新建任务', exact: true }).click();
        await page.getByRole('heading', { name: '新对话', exact: true }).waitFor();
        assert.equal(new URL(page.url()).searchParams.has('task'), false);
    });
    await scenario('loading UX keeps sidebar nodes and defers slow history placeholder', async page => {
        const workspace = await project(page, '无闪烁项目');
        const first = await emptyTask(page, workspace, '列表保持任务');
        const second = await emptyTask(page, workspace, '历史延迟任务');
        await openTask(page, first);
        const list = tasks(page, workspace.name);
        await list.getByRole('button', { name: second.title, exact: true }).waitFor();
        await list.evaluate(node => { window.savedTaskList = node; });
        let listRequests = 0;
        page.on('request', req => { if (req.url().endsWith(`/workspaces/${workspace.external_id}/tasks`) && req.method() === 'GET') listRequests++; });
        await openDraft(page, workspace.name);
        assert.equal(await list.evaluate(node => node === window.savedTaskList), true);
        assert.equal(await page.getByRole('status', { name: '正在读取任务列表' }).count(), 0);
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        await page.route(`**${taskPath(second)}/messages`, async route => {
            await gate;
            await route.continue();
        });
        await list.getByRole('button', { name: second.title, exact: true }).click();
        assert.equal(await list.evaluate(node => node === window.savedTaskList), true);
        assert.equal(listRequests, 0);
        assert.equal(await page.getByLabel('你的问题').isDisabled(), true);
        const placeholder = page.getByRole('status', { name: '正在读取对话', exact: true });
        await placeholder.waitFor();
        assert.equal(await placeholder.locator('.sr-only').count(), 1);
        await page.screenshot({ path: `${artifacts}/history-loading-placeholder.png`, animations: 'disabled' });
        release();
        await placeholder.waitFor({ state: 'hidden' });
        assert.equal(await page.getByLabel('你的问题').isEnabled(), true);
        assert.equal(listRequests, 0);
        // 后台刷新迟到时保留已显示列表；新 revision 不能销毁 DOM。
        let refreshRelease;
        const refreshGate = new Promise(resolve => { refreshRelease = resolve; });
        await page.route(`**/api/workspaces/${workspace.external_id}/tasks`, async route => {
            await refreshGate;
            await route.continue();
        });
        await page.getByRole('button', { name: `${workspace.name} 项目操作`, exact: true }).click();
        await page.getByRole('menuitem', { name: '刷新任务', exact: true }).click();
        assert.equal(await list.evaluate(node => node === window.savedTaskList), true);
        assert.equal(await list.getByRole('button', { name: first.title, exact: true }).isVisible(), true);
        assert.equal(await page.getByRole('status', { name: '正在读取任务列表' }).count(), 0);
        refreshRelease();
    });
    console.log(`Task deletion UI: ${passed} passed, ${failures.length} failed`);
    if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }
