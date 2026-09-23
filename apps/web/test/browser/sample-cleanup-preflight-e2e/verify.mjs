import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';


const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(await readFile(process.env.EXECUTION_FIXTURE, 'utf8'));
const base = process.env.PREFLIGHT_BASE;
const output = process.env.PREFLIGHT_OUTPUT;
const sourceUrl = `${base}/api/workspaces/${fixture.workspace_id}/tasks/${fixture.task_id}/sample-cleanup-preflight`;
const siblingUrl = `${base}/api/workspaces/${fixture.workspace_id}/tasks/`
    + `${process.env.PREFLIGHT_SIBLING_TASK_ID}/sample-cleanup-preflight`;
const unknownUrl = `${base}/api/workspaces/${fixture.workspace_id}/tasks/${'f'.repeat(32)}/sample-cleanup-preflight`;
const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_EXECUTABLE,
});

try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        if (request.url().includes('/api/')) {
            requests.push({
                method: request.method(),
                url: request.url(),
                headers: request.headers(),
            });
        }
    });

    await page.goto(base);
    const panel = page.getByRole('region', { name: '清理待办只读诊断', exact: true });
    const query = () => panel.getByRole('button', { name: '查询清理诊断' });
    await query().waitFor();
    assert.equal(requests.length, 0);

    // 来源任务必须由真实工作台组件经浏览器、Next BFF 和 FastAPI 读取。
    const sourceResponse = page.waitForResponse(response => response.url() === sourceUrl);
    await query().focus();
    await page.keyboard.press('Enter');
    const source = await sourceResponse;
    assert.equal(source.status(), 200);
    assert.equal(source.headers()['cache-control'], 'no-store');
    assert.equal(source.headers()['set-cookie'], undefined);
    assert.deepEqual(await source.json(), {
        workspace_id: fixture.workspace_id,
        task_id: fixture.task_id,
        result: 'identity_matches_record',
    });
    await panel.getByText('身份与记录一致').waitFor();
    await panel.getByText(/不能据此清理/).waitFor();
    assert.equal(requests.filter(request => request.url === sourceUrl).length, 1);

    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await panel.scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: `${output}/real-preflight-${width}.png`, fullPage: true });
    }

    // 兄弟 Task 与不存在的 Task 均不得看到来源任务的诊断分类。
    await page.getByRole('button', { name: '同项目其他任务' }).click();
    const siblingResponse = page.waitForResponse(response => response.url() === siblingUrl);
    await query().click();
    const sibling = await siblingResponse;
    assert.equal(sibling.status(), 404);
    assert.equal(sibling.headers()['cache-control'], 'no-store');
    // 真实组件会主动丢弃错误正文；浏览器只依据 HTTP 分类展示未知状态。
    await panel.getByText('诊断查询失败，当前目录状态未知').waitFor();
    assert.ok(!(await panel.innerText()).includes('身份与记录一致'));

    await page.getByRole('button', { name: '未知任务' }).click();
    const unknownResponse = page.waitForResponse(response => response.url() === unknownUrl);
    await query().click();
    const unknown = await unknownResponse;
    assert.equal(unknown.status(), 404);
    assert.equal(unknown.headers()['cache-control'], 'no-store');
    await panel.getByText('诊断查询失败，当前目录状态未知').waitFor();

    const invalid = await page.request.get(`${sourceUrl}?result=directory_missing`);
    assert.equal(invalid.status(), 422);
    assert.equal(invalid.headers()['cache-control'], 'no-store');
    assert.deepEqual(await invalid.json(), {
        code: 'sample_cleanup_preflight_read_failed',
        message: '读取样例清理诊断失败，不能据此判断目录状态',
    });

    assert.deepEqual(errors, []);
    assert.equal(requests.filter(request => request.method !== 'GET').length, 0);
    assert.equal(requests.filter(request => request.url === siblingUrl).length, 1);
    assert.equal(requests.filter(request => request.url === unknownUrl).length, 1);
    assert.ok(requests.every(request => !('x-local-runtime-token' in request.headers)));
    await writeFile(`${output}/browser.json`, JSON.stringify({
        browserUrl: sourceUrl,
        sourceResult: 'identity_matches_record',
        siblingStatus: 404,
        unknownStatus: 404,
        invalidQuery: 422,
        sourcePageRequests: requests.filter(request => request.url === sourceUrl).length,
        siblingPageRequests: requests.filter(request => request.url === siblingUrl).length,
        unknownPageRequests: requests.filter(request => request.url === unknownUrl).length,
        pageErrors: errors,
        writes: 0,
        widths: [1366, 1920],
    }, null, 4));
    console.log('PASS real sample cleanup preflight: browser -> Next -> FastAPI, source/404/422, no writes');
} finally {
    await browser.close();
}
