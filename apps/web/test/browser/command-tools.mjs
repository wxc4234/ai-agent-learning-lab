import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [];
let posts = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++; });
async function setup(name) {
    const project = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name } });
    assert.equal(project.status(), 201);
    const workspace = await project.json();
    const created = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: name } });
    assert.equal(created.status(), 201);
    const task = await created.json();
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').waitFor();
    return { workspace, task };
}
async function start(marker) {
    await page.getByLabel('你的问题').fill(`[command-${marker}] 执行隔离命令`);
    const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
    await page.getByLabel('你的问题').press('Enter');
    const response = await pending;
    assert.equal(response.status(), 200);
    return response.headers()['x-run-id'];
}
async function detail(id, terminal) {
    for (let attempt = 0; attempt < 100; attempt++) {
        const response = await page.request.get(`${base}/api/runs/${id}`);
        assert.equal(response.status(), 200);
        const value = await response.json();
        if (value.status === terminal) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('run did not reach terminal state');
}
try {
    for (const [marker, code, truncated] of (process.env.BROWSER_SCENARIO === 'cancel' ? [] : [['success', 0, false], ['nonzero', 7, false], ['large', 0, true]])) {
        await setup(`命令验收-${marker}`);
        const id = await start(marker);
        const answer = `命令完成：退出码${code}，截断${truncated}`;
        await page.getByText(answer, { exact: true }).waitFor();
        const run = await detail(id, 'done');
        const results = run.events.filter(event => event.event_type === 'TOOL_CALL_RESULT');
        assert.equal(results.length, 1);
        const result = JSON.parse(results[0].payload.result);
        assert.equal(result.exit_code, code);
        assert.equal(result.stdout_truncated, truncated);
        assert.equal(result.stdout.length, truncated ? 65536 : 'BROWSER_COMMAND_OK\n'.length);
        assert.equal(result.oom_killed, false);
        assert.equal(result.daemon_error, false);
        for (const key of ['execution_token', 'container_id', 'recovery_journal']) assert.ok(!JSON.stringify(run.events).includes(key));
        const expand = page.getByRole('button', { name: '展开详情', exact: true });
        if (await expand.isVisible()) await expand.click();
        await page.getByText('run_command', { exact: true }).waitFor();
        await page.getByText(code === 0 ? '命令成功' : '命令失败', { exact: true }).waitFor();
        await page.getByText('调用完成', { exact: true }).waitFor();
        const stdout = page.getByLabel('stdout', { exact: true });
        assert.equal(await stdout.textContent(), result.stdout);
        await stdout.focus();
        assert.equal(await stdout.evaluate(el => el === document.activeElement), true);
        assert.ok(await stdout.evaluate(el => el.clientHeight <= 192));
        if (truncated) {
            await page.getByText('输出已截断，仅显示已捕获部分', { exact: true }).waitFor();
            assert.ok(await stdout.evaluate(el => el.scrollHeight > el.clientHeight));
        }
        assert.equal(await page.getByLabel('stderr', { exact: true }).textContent(), '（无输出）');
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/command-${marker}-${width}.png` });
        }
        const before = posts;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        assert.equal(posts, before, 'reload must not re-execute');
        assert.equal((await detail(id, 'done')).events.filter(event => event.event_type === 'TOOL_CALL_RESULT').length, 1);
        console.log(`PASS ${marker}: keyboard/BFF/real Docker/result/PC widths/persistence/reload`);
    }
    if (process.env.BROWSER_SCENARIO !== 'cancel') {
        await setup('命令参数拒绝');
        const rejected = await start('rejected');
        await page.getByText('命令错误：command_request_rejected', { exact: true }).waitFor();
        const failed = await detail(rejected, 'done');
        assert.equal(failed.events.filter(event => event.event_type === 'TOOL_CALL_ERROR')[0].payload.details, 'command_request_rejected');
        console.log('PASS rejected command: safe error and model continuation');
    }
    await setup('命令取消');
    const cancelled = await start('cancel');
    // 等待命令启动；最终还会检查恢复证据start_attempted/stop_confirmed。
    await page.waitForTimeout(2000);
    const response = page.waitForResponse(r => /\/api\/runs\/\d+\/cancel$/.test(r.url()));
    await page.getByRole('button', { name: '停止生成', exact: true }).click();
    assert.equal((await response).status(), 204);
    await page.getByText('已停止生成', { exact: true }).waitFor({ state: 'attached' });
    const stopped = await detail(cancelled, 'aborted');
    assert.equal(stopped.events.filter(event => event.event_type === 'TOOL_CALL_RESULT').length, 0);
    const expandStopped = page.getByRole('button', { name: '展开详情', exact: true });
    if (await expandStopped.isVisible()) await expandStopped.click();
    await page.getByText('已停止生成', { exact: true }).waitFor({ state: 'visible' });
    await page.getByText('结果未确认', { exact: true }).waitFor({ state: 'visible' });
    await page.screenshot({ path: `${output}/command-cancel.png` });
    await page.reload();
    assert.equal((await detail(cancelled, 'aborted')).status, 'aborted');
    console.log('PASS user stop: aborted persisted, no false tool success');
    assert.deepEqual(errors, []);
} catch (error) {
    await page.screenshot({ path: `${output}/command-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
