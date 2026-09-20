import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const area = () => page.getByRole('region', { name: '历史运行详情', exact: true });
const card = (failed = false) => area().getByRole('region', { name: failed ? '失败运行摘要' : '运行摘要', exact: true });
try {
    const created = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name: '历史摘要验收' } });
    assert.equal(created.status(), 201);
    const workspace = await created.json();
    const taskResponse = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: '刷新恢复摘要' } });
    assert.equal(taskResponse.status(), 201);
    const task = await taskResponse.json();
    // 使用真实写入链路生成终态和指标，模型由隔离服务替换。
    const stream = await page.request.post(`${base}/api/chat/stream`, { headers: { Origin: base }, data: { session_id: task.conversation_id, prompt: '历史摘要恢复验收' } });
    assert.equal(stream.status(), 200);
    const runId = stream.headers()['x-run-id'];
    assert.ok(runId);
    const terminal = (await stream.text()).split('\n').filter(Boolean).map(JSON.parse).find(event => event.type === 'RUN_FINISHED');
    assert.ok(terminal);
    let chatPosts = 0;
    page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/api/chat/stream')) chatPosts++; });
    async function openDetail() {
        if (await page.getByRole('button', { name: '展开详情', exact: true }).isVisible()) await page.getByRole('button', { name: '展开详情', exact: true }).click();
        await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
    }
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').waitFor();
    await page.getByRole('button', { name: /展开详情|收起详情/ }).waitFor();
    await openDetail();
    await card().waitFor();
    const before = await card().innerText();
    assert.ok(before.includes(`${terminal.steps_taken} 步`));
    const cost = terminal.metrics.estimated_cost_cny;
    assert.ok(before.includes(cost === null ? '暂无数据' : `¥${cost}`));
    await page.screenshot({ path: `${output}/historical-summary-1366.png` });
    await page.reload();
    await page.getByLabel('你的问题').waitFor();
    await openDetail();
    await card().waitFor();
    assert.equal(await card().innerText(), before);
    assert.equal(chatPosts, 0);
    console.log('PASS real persisted summary survives reload/reselect without model invocation');

    const detailResponse = await page.request.get(`${base}/api/runs/${runId}`);
    assert.equal(detailResponse.status(), 200);
    const detail = await detailResponse.json();
    let mode = 'error';
    const metrics = { ...terminal.metrics, model_usage: null, model_duration_ms: null, tool_duration_ms: 0, estimated_cost_cny: '0.00128420' };
    await page.route(`**/api/runs/${runId}`, async route => {
        const payload = mode === 'missing' ? { code: 'model_error', message: '失败' } : { code: 'max_steps_exceeded', message: '失败', steps_taken: 2, metrics };
        const events = [{ id: 1, event_type: 'RUN_ERROR', created_at: detail.finished_at, payload }];
        if (mode === 'conflict') events.push({ id: 2, event_type: 'RUN_ABORTED', created_at: detail.finished_at, payload: { reason: 'user' } });
        await route.fulfill({ json: { ...detail, status: 'error', events } });
    });
    await area().getByRole('button', { name: '返回列表', exact: true }).click();
    await openDetail();
    await card(true).waitFor();
    const failed = await card(true).innerText();
    assert.ok(failed.includes('2 步') && failed.includes('¥0.00128420') && failed.includes('0 ms'));
    const values = await card(true).locator('dd').allTextContents();
    assert.equal(values.filter(value => value === '暂无数据').length, 2);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.screenshot({ path: `${output}/historical-summary-error-1920.png` });
    console.log('PASS failed summary preserves unknown values, actual zero and decimal precision');
    for (const next of ['missing', 'conflict']) {
        mode = next;
        await area().getByRole('button', { name: '返回列表', exact: true }).click();
        await openDetail();
        await area().getByText('暂无可用的运行摘要，可查看下方已记录的事件。', { exact: true }).waitFor();
        assert.equal(await card(true).count(), 0);
        assert.equal(await area().locator('ol li').count(), next === 'conflict' ? 2 : 1);
        console.log(`PASS ${next} summary does not invent metrics; timeline retained`);
    }
    assert.equal(chatPosts, 0);
    assert.deepEqual(errors, []);
} catch (error) {
    await page.screenshot({ path: `${output}/historical-summary-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
