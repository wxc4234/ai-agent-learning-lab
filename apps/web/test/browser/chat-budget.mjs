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
const first = await context.newPage();
const second = await context.newPage();
const errors = [];
for (const page of [first, second]) {
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => errors.push(error.message));
}
try {
    const project = await first.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name: '执行占用验收' } });
    assert.equal(project.status(), 201);
    const workspace = await project.json();
    const created = await first.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '并发执行验收' } });
    assert.equal(created.status(), 201);
    const task = await created.json();
    const secondCreated = await first.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '另一个会话的容量验收' } });
    assert.equal(secondCreated.status(), 201);
    const otherTask = await secondCreated.json();
    for (const page of [first, second]) {
        const selectedTask = page === first ? task : otherTask;
        const url = `${base}/?workspace=${workspace.external_id}&task=${selectedTask.external_id}`;
        await page.bringToFront();
        const loaded = page.waitForResponse(r => r.url().endsWith(`/tasks/${selectedTask.external_id}/messages`) && r.status() === 200);
        await page.goto(url);
        await loaded;
        await page.waitForFunction(() => {
            const input = document.querySelector('textarea');
            return input && !input.disabled;
        });
    }
    console.log('READY both restored task histories');
    await first.bringToFront();
    await first.getByLabel('你的问题').fill('[cancel-test] 保持第一轮运行');
    const started = first.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
    await first.getByLabel('你的问题').press('Enter');
    assert.equal((await started).status(), 200);
    console.log("READY first stream owns execution");
    const rejected = second.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
    await second.bringToFront();
    await second.getByLabel('你的问题').fill('第二个标签页不能并发执行');
    await second.getByLabel('你的问题').press('Enter');
    assert.equal((await rejected).status(), 503);
    await second.getByText('执行服务暂时繁忙或不可用，请稍后再试。', { exact: true }).waitFor();
    await second.screenshot({ path: `${output}/budget-busy-1366.png` });
    console.log('PASS different conversation receives shared-budget 503 and safe UI message');

    const cancelled = first.waitForResponse(r => /\/api\/runs\/\d+\/cancel$/.test(r.url()));
    await first.bringToFront();
    await first.getByRole('button', { name: '停止生成', exact: true }).click();
    assert.equal((await cancelled).status(), 204);
    await first.getByText('已停止生成', { exact: true }).waitFor({ state: 'attached' });
    // 测试主动重试并观察响应；不能把取消接口的终态直接当成释放证据。
    let recovered = false;
    for (let attempt = 0; attempt < 15; attempt++) {
        const response = await second.request.post(`${base}/api/chat/stream`, {
            headers: { Origin: base }, data: { session_id: otherTask.conversation_id, prompt: '取消后恢复验收' },
        });
        if (response.status() === 503) {
            await new Promise(resolve => setTimeout(resolve, 150));
            continue;
        }
        assert.equal(response.status(), 200);
        assert.match(await response.text(), /RUN_FINISHED/);
        recovered = true;
        break;
    }
    assert.ok(recovered, 'execution slot was never released');
    await second.bringToFront();
    await second.reload();
    await second.getByText('取消后恢复验收', { exact: true }).waitFor();
    await second.setViewportSize({ width: 1920, height: 900 });
    await second.screenshot({ path: `${output}/budget-recovered-1920.png` });
    console.log('PASS cancellation drains execution, releases slot and persisted history reloads');
    assert.deepEqual(errors, []);
} catch (error) {
    await first.screenshot({ path: `${output}/budget-first-failure.png`, timeout: 5000 }).catch(() => {});
    console.error("PAGE ERRORS", errors);
    await second.screenshot({ path: `${output}/budget-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
