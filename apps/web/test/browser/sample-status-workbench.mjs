import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') {
    throw new Error('Use the isolated local-mode browser launcher');
}

const output = fileURLToPath(new URL('../../output/playwright/sample-status-workbench/', import.meta.url));
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });

try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    const errors = [];
    const browserRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (request.url().includes('/api/')) {
            browserRequests.push({ method: request.method(), url: request.url() });
        }
    });

    // 夹具资源通过真实BFF创建在随机测试库；浏览器从未得到样例登记能力。
    const workspaceResponse = await page.request.post(`${base}/api/workspaces`, {
        headers: { Origin: base },
        data: { name: '状态验收项目' },
    });
    assert.equal(workspaceResponse.status(), 201);
    const workspace = await workspaceResponse.json();
    const tasks = [];
    for (const title of ['登记任务甲', '登记任务乙']) {
        const response = await page.request.post(
            `${base}/api/workspaces/${workspace.external_id}/tasks`,
            { headers: { Origin: base }, data: { title } },
        );
        assert.equal(response.status(), 201);
        tasks.push(await response.json());
    }

    const statusUrl = task => `${base}/api/workspaces/${workspace.external_id}`
        + `/tasks/${task.external_id}/sample-status`;
    const preflightUrl = task => `${base}/api/workspaces/${workspace.external_id}`
        + `/tasks/${task.external_id}/sample-cleanup-preflight`;
    const [firstUrl, secondUrl] = tasks.map(statusUrl);
    const sampleRequests = () => browserRequests.filter(request => request.url.endsWith('/sample-status'));
    const preflightRequests = () => browserRequests.filter(request => request.url.endsWith('/sample-cleanup-preflight'));

    await page.goto(base);
    const taskList = page.getByRole('list', { name: '状态验收项目 任务', exact: true });
    await taskList.getByRole('button', { name: '登记任务甲', exact: true }).click();
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    const panel = page.locator('#workbench-details')
        .getByRole('region', { name: '受限样例登记状态', exact: true });
    const preflightPanel = page.locator('#workbench-details')
        .getByRole('region', { name: '清理待办只读诊断', exact: true });
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();
    await preflightPanel.getByText('尚未查询当前任务的清理诊断。').waitFor();
    assert.equal(sampleRequests().length, 0);
    assert.equal(preflightRequests().length, 0);

    // 当前任务点击后才从真实工作台经同源BFF到真实FastAPI。
    const firstResponse = page.waitForResponse(response => response.url() === firstUrl);
    await panel.getByRole('button', { name: '查询登记状态' }).focus();
    await page.keyboard.press('Enter');
    const first = await firstResponse;
    assert.equal(first.status(), 200);
    assert.equal(first.headers()['cache-control'], 'no-store');
    assert.equal(first.headers()['set-cookie'], undefined);
    assert.deepEqual(await first.json(), {
        workspace_id: workspace.external_id,
        task_id: tasks[0].external_id,
        status: 'missing',
        sealed_reason: null,
    });
    await panel.getByText('查询时没有该任务的进程内样例登记').waitFor();
    assert.deepEqual(sampleRequests(), [{ method: 'GET', url: firstUrl }]);

    // 新面板也只在用户触发后读取，真实工作台经BFF和API返回只读分类。
    const firstPreflightUrl = preflightUrl(tasks[0]);
    const preflightResponse = page.waitForResponse(response => response.url() === firstPreflightUrl);
    await preflightPanel.getByRole('button', { name: '查询清理诊断' }).focus();
    await page.keyboard.press('Enter');
    const preflight = await preflightResponse;
    assert.equal(preflight.status(), 200);
    assert.equal(preflight.headers()['cache-control'], 'no-store');
    assert.equal(preflight.headers()['set-cookie'], undefined);
    assert.deepEqual(await preflight.json(), {
        workspace_id: workspace.external_id,
        task_id: tasks[0].external_id,
        result: 'evidence_missing',
    });
    await preflightPanel.getByText('缺少来源证据', { exact: false }).waitFor();
    assert.deepEqual(preflightRequests(), [{ method: 'GET', url: firstPreflightUrl }]);
    assert.equal(await preflightPanel.getByRole('button', { name: /执行清理|恢复登记|重试应用/ }).count(), 0);

    // 收起会卸载查询状态；重新展开不能自动请求或沿用旧快照。
    await page.getByRole('button', { name: '收起详情', exact: true }).click();
    assert.equal(await panel.count(), 0);
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();
    await preflightPanel.getByText('尚未查询当前任务的清理诊断。').waitFor();
    assert.equal(sampleRequests().length, 1);
    assert.equal(preflightRequests().length, 1);

    await taskList.getByRole('button', { name: '登记任务乙', exact: true }).click();
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();
    await preflightPanel.getByText('尚未查询当前任务的清理诊断。').waitFor();
    assert.equal(sampleRequests().length, 1);
    assert.equal(preflightRequests().length, 1);

    // 查询失败是未知状态，不能回退成没有登记；恢复后可按需重查。
    await page.route(secondUrl, route => route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: '{"detail":"isolated failure"}',
    }));
    const failedResponse = page.waitForResponse(response => response.url() === secondUrl);
    await panel.getByRole('button', { name: '查询登记状态' }).click();
    assert.equal((await failedResponse).status(), 502);
    await panel.getByRole('alert').waitFor();
    assert.ok((await panel.innerText()).includes('当前状态未知'));
    assert.ok(!(await panel.innerText()).includes('查询时没有该任务的进程内样例登记'));
    await page.unroute(secondUrl);

    const secondResponse = page.waitForResponse(response => response.url() === secondUrl);
    await panel.getByRole('button', { name: '重新查询登记状态' }).click();
    const second = await secondResponse;
    assert.equal(second.status(), 200);
    assert.deepEqual(await second.json(), {
        workspace_id: workspace.external_id,
        task_id: tasks[1].external_id,
        status: 'missing',
        sealed_reason: null,
    });
    await panel.getByText('查询时没有该任务的进程内样例登记').waitFor();

    // 旧Task请求被延迟到切换之后，结果也不得进入新Task面板。
    let started;
    let release;
    let finished;
    const slowStarted = new Promise(resolve => { started = resolve; });
    const slowGate = new Promise(resolve => { release = resolve; });
    const slowFinished = new Promise(resolve => { finished = resolve; });
    let delayedDelivery = 'delivered';
    await page.route(firstUrl, async route => {
        started();
        await slowGate;
        try {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    workspace_id: workspace.external_id,
                    task_id: tasks[0].external_id,
                    status: 'ready',
                    sealed_reason: null,
                }),
            });
        } catch {
            delayedDelivery = 'canceled';
        } finally {
            finished();
        }
    });
    await taskList.getByRole('button', { name: '登记任务甲', exact: true }).click();
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();
    await panel.getByRole('button', { name: '查询登记状态' }).click();
    await slowStarted;
    await taskList.getByRole('button', { name: '登记任务乙', exact: true }).click();
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();
    release();
    await slowFinished;
    await page.waitForTimeout(100);
    assert.ok((await panel.innerText()).includes('尚未查询当前任务'));
    assert.ok(!(await panel.innerText()).includes('查询时登记可供后续门禁检查'));
    await page.unroute(firstUrl);

    await page.getByRole('button', { name: '在 状态验收项目 新建任务', exact: true }).click();
    assert.equal(await panel.count(), 0);
    await taskList.getByRole('button', { name: '登记任务甲', exact: true }).click();
    await panel.getByText('尚未查询当前任务的样例登记。').waitFor();

    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: `${output}/workbench-${width}.png`, animations: 'disabled' });
    }

    assert.deepEqual(errors, []);
    assert.equal(browserRequests.filter(request => request.method !== 'GET').length, 0);
    await writeFile(`${output}/evidence.json`, JSON.stringify({
        workspaceId: workspace.external_id,
        taskIds: tasks.map(task => task.external_id),
        statusRequests: sampleRequests(),
        preflightRequests: preflightRequests(),
        delayedDelivery,
        pageErrors: errors,
        browserWrites: 0,
        viewports: [1366, 1920],
        noHorizontalOverflow: true,
    }, null, 4));
    console.log('PASS full workbench sample status and cleanup preflight: real GET, task switch, collapse, stale response, no writes');
} finally {
    await browser.close();
}
