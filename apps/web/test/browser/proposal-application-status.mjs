import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-application-status/output/playwright';
await mkdir(output, { recursive: true });
const fixture = await mkdtemp('/private/tmp/agent-proposal-');
const root = `${await realpath(fixture)}/project`;
await mkdir(root);
const sources = {
    success: '\ufeffcount = old\r\n',
    delete: 'old',
    preview: 'old',
    ambiguous: 'old old\n',
    truncated: 'old' + 'X'.repeat(20000),
};
for (const [name, content] of Object.entries(sources)) await writeFile(`${root}/${name}.txt`, content);
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let posts = 0;
let detailGets = 0;
page.on('request', request => {
    if (request.method() === 'GET' && request.url().includes('/file-edit-proposals/')) detailGets++;
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
});
async function setup(name, bound) {
    const response = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(response.status(), 201);
    const workspace = await response.json();
    if (bound) {
        const binding = await page.request.put(`${base}/api/workspaces/${workspace.external_id}/directory`, {
            headers: { Origin: base }, data: { root_path: root },
        });
        assert.equal(binding.status(), 200);
    }
    const created = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, {
        headers: { Origin: base }, data: { title: name },
    });
    assert.equal(created.status(), 201);
    const task = await created.json();
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').waitFor();
    return { workspace, task };
}
async function send(prompt, answer) {
    await page.getByLabel('你的问题').fill(prompt);
    const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
    // 键盘发送，经过真实浏览器→BFF→API，不拦截聊天响应。
    await page.getByLabel('你的问题').press('Enter');
    const response = await pending;
    assert.equal(response.status(), 200);
    await page.getByText(answer, { exact: true }).waitFor();
    const runId = response.headers()['x-run-id'];
    // Chrome 对已消费的流式正文不保证可再次读取；页面验证展示，GET 验证持久化事件。
    let events = [];
    for (let attempt = 0; attempt < 30; attempt++) {
        const stored = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(stored.status(), 200);
        const detail = await stored.json();
        events = detail.events.map(event => ({ ...event.payload, type: event.event_type }));
        if (events.some(event => event.type === 'RUN_FINISHED')) break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.equal(events.filter(event => event.type === 'RUN_FINISHED').length, 1);
    const expand = page.getByRole('button', { name: '展开详情', exact: true });
    if (await expand.isVisible()) await expand.click();
    return { events, runId };
}

async function openDetail() {
    const panel = page.getByRole('region', { name: '文件修改提案详情', exact: true });
    await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
    await panel.getByLabel('提案 Diff', { exact: true }).waitFor();
    return panel;
}
try {
    const { workspace, task } = await setup('应用状态查询', true);
    const { events } = await send('[proposal-success] 保存待审批提案', '提案已保存，等待审批，文件尚未修改');
    const result = JSON.parse(events.find(event => event.type === 'TOOL_CALL_RESULT').result);
    const endpoint = `${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/file-edit-proposals/${result.proposal_id}/application-status`;
    let queries = 0;
    let writes = 0;
    page.on('request', request => {
        if (request.url() === endpoint) queries++;
        if (request.method() !== 'GET' && request.url().includes('/file-edit-proposals/')) writes++;
    });
    let detail = await openDetail();
    const panel = () => page.getByRole('region', { name: '提案应用状态', exact: true });
    assert.equal(queries, 0);
    if (process.env.BROWSER_STATUS_EXTRA_ONLY === '1') {
        for (const action of ['cancel', 'switch']) {
            let release;
            const gate = new Promise(resolve => { release = resolve; });
            let entered;
            const started = new Promise(resolve => { entered = resolve; });
            await page.route(endpoint, async route => {
                entered();
                await gate;
                await route.fulfill({ json: { proposal_id: result.proposal_id,
                    workspace_id: workspace.external_id, task_id: task.external_id,
                    application_status: 'applied' } }).catch(() => {});
            });
            await panel().getByRole('button', { name: '查询应用状态', exact: true }).click();
            await started;
            if (action === 'cancel') {
                await panel().getByRole('button', { name: '取消状态查询', exact: true }).click();
            } else {
                const created = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, {
                    headers: { Origin: base }, data: { title: '其他任务' },
                });
                assert.equal(created.status(), 201);
                const other = await created.json();
                await page.goto(`${base}/?workspace=${workspace.external_id}&task=${other.external_id}`);
                await page.getByLabel('你的问题').waitFor();
                assert.equal(await panel().count(), 0);
            }
            release();
            await page.unrouteAll({ behavior: 'wait' });
            if (action === 'cancel') {
                await panel().getByRole('button', { name: '查询应用状态', exact: true }).waitFor();
                assert.ok(!(await panel().innerText()).includes('已登记应用成功'));
            } else {
                assert.equal(await panel().count(), 0);
            }
        }
        assert.equal(writes, 0);
        assert.equal(await readFile(`${root}/success.txt`, 'utf8'), sources.success);
        assert.deepEqual(errors, []);
        console.log('PASS explicit cancel and cross-task navigation ignore delayed responses');
    } else {
    await panel().getByRole('button', { name: '查询应用状态', exact: true }).focus();
    await page.keyboard.press('Enter');
    await panel().getByText('查询时应用状态：尚未领取执行', { exact: true }).waitFor();
    assert.equal(queries, 1);
    const payload = { proposal_id: result.proposal_id, workspace_id: workspace.external_id, task_id: task.external_id };
    for (const [status, label] of [['running', '已领取，尚无终态记录'], ['applied', '已登记应用成功'],
        ['not_applied', '已登记本次未应用'], ['uncertain', '结果或清理未确认']]) {
        await page.route(endpoint, route => route.fulfill({ json: { ...payload, application_status: status } }));
        await panel().getByRole('button', { name: '重新查询应用状态', exact: true }).click();
        await panel().getByText(`查询时应用状态：${label}`, { exact: true }).waitFor();
        await page.unroute(endpoint);
    }
    for (const bad of ['unknown', 'mismatch', '404']) {
        await page.route(endpoint, route => route.fulfill(bad === '404' ? { status: 404, body: 'PRIVATE' } : { json: {
            ...payload, proposal_id: bad === 'mismatch' ? 'f'.repeat(32) : result.proposal_id,
            application_status: bad === 'mismatch' ? 'applied' : 'unknown',
        } }));
        await panel().getByRole('button', { name: '重新查询应用状态', exact: true }).click();
        await panel().getByRole('alert').waitFor();
        assert.ok(!(await panel().innerText()).includes('PRIVATE'));
        assert.ok(!(await panel().innerText()).includes('尚未领取执行'));
        await page.unroute(endpoint);
    }
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    await page.route(endpoint, async route => {
        entered();
        await gate;
        await route.fulfill({ json: { ...payload, application_status: 'applied' } }).catch(() => {});
    });
    await panel().getByRole('button', { name: '重新查询应用状态', exact: true }).click();
    await started;
    await detail.getByRole('button', { name: '收起详情', exact: true }).click();
    release();
    await page.unrouteAll({ behavior: 'wait' });
    detail = await openDetail();
    await panel().getByRole('button', { name: '查询应用状态', exact: true }).waitFor();
    assert.ok(!(await panel().innerText()).includes('查询时应用状态：'));
    await panel().getByRole('button', { name: '查询应用状态', exact: true }).click();
    await panel().getByText('查询时应用状态：尚未领取执行', { exact: true }).waitFor();
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await panel().scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: `${output}/application-status-${width}.png`, fullPage: true });
    }
    assert.equal(writes, 0);
    assert.equal(await readFile(`${root}/success.txt`, 'utf8'), sources.success);
    assert.deepEqual(errors, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify({ queries, writes, proposalId: result.proposal_id,
        realState: 'idle', mockedStates: ['running', 'applied', 'not_applied', 'uncertain'], widths: [1366, 1920] }, null, 2));
    console.log('PASS application status: real idle, mocked states, errors, stale response, keyboard, two PC widths');
    }
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
}
