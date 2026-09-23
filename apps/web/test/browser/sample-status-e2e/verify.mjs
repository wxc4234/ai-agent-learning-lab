import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(await readFile(process.env.EXECUTION_FIXTURE, 'utf8'));
const base = process.env.STATUS_BASE;
const output = process.env.STATUS_OUTPUT;
const expectedStatus = process.env.STATUS_EXPECTED;
const expectedReason = process.env.STATUS_SEALED_REASON === 'null'
    ? null : process.env.STATUS_SEALED_REASON;
assert.ok(expectedStatus === 'ready' || expectedStatus === 'sealed');
assert.ok(expectedReason === null || expectedReason === 'cleanup_pending' || expectedReason === 'unavailable');
assert.equal(expectedStatus === 'sealed', expectedReason !== null);
const sampleUrl = `/api/workspaces/${fixture.workspace_id}/tasks/${fixture.task_id}/sample-status`;
const applicationUrl = `/api/workspaces/${fixture.workspace_id}/tasks/${fixture.task_id}`
    + `/file-edit-proposals/${fixture.proposal_id}/application-status`;
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });

try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (request.url().includes('/api/')) requests.push({ method: request.method(), url: request.url() });
    });

    await page.goto(base);
    const detail = page.getByRole('region', { name: '文件修改提案详情', exact: true });
    await detail.getByRole('button', { name: '查看提案详情' }).waitFor();
    assert.equal(requests.length, 0);
    await detail.getByRole('button', { name: '查看提案详情' }).click();
    const review = detail.getByRole('region', { name: '样例与提案状态核对', exact: true });
    const sample = review.getByRole('region', { name: '受限样例登记状态', exact: true });
    const application = review.getByRole('region', { name: '提案应用状态', exact: true });
    await sample.getByRole('button', { name: '查询登记状态' }).waitFor();
    assert.equal(requests.filter(request => request.url === base + sampleUrl).length, 0);

    const sampleResponse = page.waitForResponse(response => response.url() === base + sampleUrl);
    await sample.getByRole('button', { name: '查询登记状态' }).focus();
    await page.keyboard.press('Enter');
    const statusResponse = await sampleResponse;
    assert.equal(statusResponse.status(), 200);
    assert.equal(statusResponse.headers()['cache-control'], 'no-store');
    assert.equal(statusResponse.headers()['set-cookie'], undefined);
    assert.deepEqual(await statusResponse.json(), {
        workspace_id: fixture.workspace_id,
        task_id: fixture.task_id,
        status: expectedStatus,
        sealed_reason: expectedReason,
    });
    const expectedLabel = expectedReason === 'cleanup_pending'
        ? '查询时样例登记处于清理待办'
        : expectedStatus === 'sealed'
            ? '查询时登记已封锁或绑定不匹配'
            : '查询时登记可供后续门禁检查';
    await sample.getByText(expectedLabel).waitFor();
    if (expectedReason === 'cleanup_pending') {
        await sample.getByText(/不能据此判断样例目录是否仍存在/).waitFor();
        assert.equal(await sample.getByRole('button', { name: /清理|重试/ }).count(), 0);
    }
    assert.equal(requests.filter(request => request.url === base + sampleUrl).length, 1);
    assert.equal(requests.filter(request => request.url === base + applicationUrl).length, 0);

    const applicationResponse = page.waitForResponse(response => response.url() === base + applicationUrl);
    await application.getByRole('button', { name: '查询应用状态' }).click();
    assert.equal((await applicationResponse).status(), 200);
    await application.getByText('查询时应用状态：尚未领取执行').waitFor();
    assert.ok((await review.innerText()).includes('不能据此执行提案'));

    const invalidQuery = await page.request.get(base + sampleUrl + '?status=ready', { headers: { Origin: base } });
    assert.equal(invalidQuery.status(), 422);
    assert.equal((await invalidQuery.json()).status, undefined);

    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await review.scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: `${output}/real-status-${width}.png`, fullPage: true });
    }

    await page.goto(`${base}/task-status`);
    const taskPanel = page.getByRole('region', { name: '受限样例登记状态', exact: true });
    await taskPanel.getByRole('button', { name: '查询登记状态' }).waitFor();
    assert.ok((await taskPanel.innerText()).includes('尚未查询'));
    const missingUrl = `${base}/api/workspaces/${process.env.STATUS_MISSING_WORKSPACE_ID}/tasks/`
        + `${process.env.STATUS_MISSING_TASK_ID}/sample-status`;
    await page.getByRole('button', { name: '未登记任务' }).click();
    const missingResponse = page.waitForResponse(response => response.url() === missingUrl);
    await taskPanel.getByRole('button', { name: '查询登记状态' }).click();
    const missing = await missingResponse;
    assert.equal(missing.status(), 200);
    assert.equal(missing.headers()['cache-control'], 'no-store');
    assert.deepEqual(await missing.json(), {
        workspace_id: process.env.STATUS_MISSING_WORKSPACE_ID,
        task_id: process.env.STATUS_MISSING_TASK_ID,
        status: 'missing',
        sealed_reason: null,
    });
    await taskPanel.getByText('查询时没有该任务的进程内样例登记').waitFor();

    const siblingUrl = `${base}/api/workspaces/${fixture.workspace_id}/tasks/`
        + `${process.env.STATUS_SIBLING_TASK_ID}/sample-status`;
    await page.getByRole('button', { name: '同项目其他任务' }).click();
    const siblingResponse = page.waitForResponse(response => response.url() === siblingUrl);
    await taskPanel.getByRole('button', { name: '查询登记状态' }).click();
    const sibling = await siblingResponse;
    assert.equal(sibling.status(), 200);
    assert.equal(sibling.headers()['cache-control'], 'no-store');
    assert.deepEqual(await sibling.json(), {
        workspace_id: fixture.workspace_id,
        task_id: process.env.STATUS_SIBLING_TASK_ID,
        status: 'sealed',
        sealed_reason: 'unavailable',
    });
    await taskPanel.getByText('查询时登记已封锁或绑定不匹配').waitFor();
    assert.ok(!(await taskPanel.innerText()).includes('清理待办'));

    const unknownUrl = `${base}/api/workspaces/${fixture.workspace_id}/tasks/${'f'.repeat(32)}/sample-status`;
    await page.getByRole('button', { name: '未知任务' }).click();
    const unknownResponse = page.waitForResponse(response => response.url() === unknownUrl);
    await taskPanel.getByRole('button', { name: '查询登记状态' }).click();
    const unknown = await unknownResponse;
    assert.equal(unknown.status(), 404);
    await taskPanel.getByRole('alert').waitFor();
    assert.ok((await taskPanel.innerText()).includes('当前状态未知'));
    assert.ok(!(await taskPanel.innerText()).includes('查询时没有该任务的进程内样例登记'));

    assert.deepEqual(errors, []);
    assert.equal(requests.filter(request => request.method !== 'GET').length, 0);
    await writeFile(`${output}/browser.json`, JSON.stringify({
        browserUrl: base + sampleUrl,
        sampleStatus: expectedStatus, sealedReason: expectedReason,
        siblingStatus: 'sealed', siblingReason: 'unavailable',
        missing: 'missing', unauthorized: 404, invalidQuery: 422,
        pageSampleRequests: requests.filter(request => request.url === base + sampleUrl).length,
        pageApplicationRequests: requests.filter(request => request.url === base + applicationUrl).length,
        missingPageRequests: requests.filter(request => request.url === missingUrl).length,
        siblingPageRequests: requests.filter(request => request.url === siblingUrl).length,
        unauthorizedPageRequests: requests.filter(request => request.url === unknownUrl).length,
        pageErrors: errors, writes: 0, widths: [1366, 1920],
    }, null, 4));
    console.log(`PASS real sample status: browser -> Next -> FastAPI, ${expectedStatus}/sealed/missing/404/422, no writes`);
} finally {
    await browser.close();
}
