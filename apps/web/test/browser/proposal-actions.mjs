import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-proposal-actions/output/playwright';
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

const evidence = [];
let decisions = 0;
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/decision')) decisions++;
});
async function until(check) {
    const deadline = Date.now() + 10000;
    while (!(await check())) {
        if (Date.now() > deadline) throw new Error('Condition timed out');
        await new Promise(resolve => setTimeout(resolve, 30));
    }
}
async function openHistory(runId) {
    const expand = page.getByRole('button', { name: '展开详情', exact: true });
    if (await expand.isVisible()) await expand.click();
    await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
    await page.getByRole('region', { name: '历史运行详情', exact: true }).waitFor();
}
async function openDetail() {
    const expand = page.getByRole('button', { name: '展开详情', exact: true });
    if (await expand.isVisible()) await expand.click();
    const panel = page.getByRole('region', { name: '文件修改提案详情', exact: true });
    await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
    await panel.getByLabel('提案 Diff', { exact: true }).waitFor();
    return panel;
}
try {
    for (const scenario of (process.env.BROWSER_ACTION_SCENARIOS || 'approve,reject,truncated,unknown,safe-error,switch').split(',')) {
        const marker = scenario === 'truncated' ? 'truncated' : 'success';
        const { workspace, task } = await setup(`审批-${scenario}`, true);
        const targetUrl = `${base}/?workspace=${workspace.external_id}&task=${task.external_id}`;
        const { events, runId } = await send(`[proposal-${marker}] 保存待审批提案`, '提案已保存，等待审批，文件尚未修改');
        const result = JSON.parse(events.find(event => event.type === 'TOOL_CALL_RESULT').result);
        const endpoint = `${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/file-edit-proposals/${result.proposal_id}`;
        const key = `file-edit-proposal-decision:v1:${workspace.external_id}:${task.external_id}:${result.proposal_id}`;
        if (process.env.BROWSER_PROPOSAL_HISTORY === '1') {
            const reads = detailGets;
            const chats = posts;
            await page.reload();
            await page.getByLabel('你的问题').waitFor();
            await openHistory(runId);
            assert.equal(detailGets, reads, 'history must not automatically fetch proposal details');
            assert.equal(posts, chats, 'history must not resend chat');
        }
        let panel = await openDetail();
        const approve = () => panel.getByRole('button', { name: '批准提案', exact: true });
        const reject = () => panel.getByRole('button', { name: '拒绝提案', exact: true });
        await until(() => reject().isEnabled());
        let expected = 'pending';
        const before = decisions;
        if (scenario === 'approve') {
            // 同一事件循环连续触发，验证ref而非仅依赖React下一次渲染禁用。
            await approve().evaluate(button => { button.click(); button.click(); });
            await panel.getByText('已确认批准此提案，尚未应用到文件。', { exact: true }).waitFor();
            assert.equal(decisions, before + 1);
            expected = 'approved';
        } else if (scenario === 'reject' || scenario === 'truncated') {
            assert.equal(await approve().isDisabled(), scenario === 'truncated');
            await reject().focus();
            await reject().press('Enter');
            await panel.getByText('已确认拒绝此提案。', { exact: true }).waitFor();
            expected = 'rejected';
        } else if (scenario === 'safe-error') {
            // 存储异常只注入浏览器；未保存标记时不得发送POST。
            await page.evaluate(() => {
                const original = Storage.prototype.setItem;
                window.restoreProposalStorage = () => { Storage.prototype.setItem = original; };
                Storage.prototype.setItem = function(key, value) {
                    if (key.startsWith('file-edit-proposal-decision:')) throw new Error('controlled storage failure');
                    return original.call(this, key, value);
                };
            });
            await approve().click();
            await panel.getByRole('alert').filter({ hasText: '无法访问本标签页' }).waitFor();
            assert.equal(decisions, before);
            await page.evaluate(() => window.restoreProposalStorage());
            await panel.getByRole('button', { name: '收起详情', exact: true }).click();
            panel = await openDetail();
            await until(() => reject().isEnabled());
            await page.route(endpoint + '/decision', route => route.fulfill({
                status: 409, json: { code: 'proposal_binding_changed', message: 'PRIVATE' },
            }), { times: 1 });
            await approve().click();
            await panel.getByRole('alert').filter({ hasText: '目录绑定已变化' }).waitFor();
            assert.ok(!(await panel.innerText()).includes('PRIVATE'));
            assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), null);
            await reject().click();
            await panel.getByText('已确认拒绝此提案。', { exact: true }).waitFor();
            expected = 'rejected';
        } else if (scenario === 'unknown') {
            await page.route(endpoint + '/decision', route => route.fulfill({ status: 500,
                json: { code: 'proposal_decision_uncertain', message: 'PRIVATE' },
            }), { times: 1 });
            await approve().click();
            await panel.getByRole('alert').filter({ hasText: '审批结果未确认' }).waitFor();
            assert.equal(await approve().isDisabled(), true);
            await panel.getByRole('button', { name: '收起详情', exact: true }).click();
            panel = await openDetail();
            await panel.getByRole('alert').filter({ hasText: '审批结果未确认' }).waitFor();
            assert.equal(await reject().isDisabled(), true);
            for (const width of [1366, 1920]) {
                await page.setViewportSize({ width, height: 900 });
                assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
                await panel.scrollIntoViewIfNeeded();
                await page.screenshot({ path: `${output}/unknown-${width}.png` });
            }
            await page.reload();
            await page.getByLabel('你的问题').waitFor();
            // 历史入口恢复后，重新查询pending仍不能解除未知结果标记。
            await openHistory(runId);
            panel = await openDetail();
            await panel.getByRole('alert').filter({ hasText: '审批结果未确认' }).waitFor();
            assert.equal(await approve().isDisabled(), true);
            assert.equal(await reject().isDisabled(), true);
            assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), 'uncertain');
            assert.equal(decisions, before + 1);
        } else {
            let release;
            let started;
            let finished;
            const held = new Promise(resolve => { release = resolve; });
            const intercepted = new Promise(resolve => { started = resolve; });
            const handled = new Promise(resolve => { finished = resolve; });
            await page.route(endpoint + '/decision', async route => {
                started();
                await held;
                await route.fulfill({ json: { proposal_id: result.proposal_id,
                    workspace_id: workspace.external_id, task_id: task.external_id, status: 'approved' } }).catch(() => {});
                finished();
            }, { times: 1 });
            await approve().click();
            await intercepted;
            assert.equal(await reject().isDisabled(), true);
            // 点击真实侧栏切到新对话，等待旧组件卸载后才释放迟到响应。
            await page.getByRole('button', { name: '新对话', exact: true }).click();
            await panel.waitFor({ state: 'detached' });
            release();
            await handled;
            assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), 'uncertain');
            assert.equal(await page.getByText('已确认批准此提案，尚未应用到文件。', { exact: true }).count(), 0);
            assert.equal(decisions, before + 1);
        }
        if (process.env.BROWSER_PROPOSAL_HISTORY === '1' && scenario !== 'unknown') {
            // 包含切到新对话后返回原Task；已确认终态来自重新授权查询。
            await page.goto(targetUrl);
            await page.getByLabel('你的问题').waitFor();
            await openHistory(runId);
            panel = await openDetail();
            if (expected === 'pending') {
                await panel.getByRole('alert').filter({ hasText: '审批结果未确认' }).waitFor();
                assert.equal(await reject().isDisabled(), true);
            } else {
                await panel.getByText(expected === 'approved'
                    ? '已确认批准此提案，尚未应用到文件。' : '已确认拒绝此提案。', { exact: true }).waitFor();
                assert.equal(await approve().count(), 0);
            }
        }
        const detailResponse = await page.request.get(endpoint);
        assert.equal(detailResponse.status(), 200);
        assert.equal((await detailResponse.json()).status, expected);
        for (const width of scenario === 'unknown' ? [] : [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            if (await panel.isVisible()) await panel.scrollIntoViewIfNeeded();
            await page.screenshot({ path: `${output}/${scenario}-${width}.png` });
        }
        for (const [name, content] of Object.entries(sources)) assert.deepEqual(await readFile(`${root}/${name}.txt`), Buffer.from(content));
        evidence.push({ scenario, proposal_id: result.proposal_id, expected, url: targetUrl });
        console.log(`PASS ${scenario}: decision/guard/file unchanged/PC`);
    }
    assert.deepEqual(errors, []);
    await writeFile(`${output}/evidence.json`, JSON.stringify(evidence));
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
    console.log('Temporary approval files removed.');
}
