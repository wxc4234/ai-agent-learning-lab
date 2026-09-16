import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const area = () => page.getByRole('region', { name: '历史运行', exact: true });
const waitCount = async count => {
    await page.waitForFunction(n => document.querySelectorAll('[aria-label="历史运行"] li').length === n, count);
};
async function create(path, data) {
    const response = await page.request.post(`${base}/api${path}`, { headers: { Origin: base }, data });
    assert.equal(response.status(), 201);
    return response.json();
}
try {
    const workspace = await create('/workspaces', { name: '运行历史验收' });
    const task = await create(`/workspaces/${workspace.external_id}/tasks`, { title: '历史分页任务' });
    const other = await create(`/workspaces/${workspace.external_id}/tasks`, { title: '切换目标任务' });
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').waitFor();
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await area().getByText('这个任务还没有运行记录。').waitFor();
    console.log('PASS real BFF/API empty task');

    const item = id => ({ run_id: id, status: id === 30 ? 'future-state' : 'done',
        started_at: '2026-09-16T00:00:00Z', finished_at: '2026-09-16T00:00:00Z', duration_ms: 0 });
    let mode = 'pages';
    let failMore = true;
    let release;
    let requested = [];
    await page.route('**/api/workspaces/*/tasks/*/runs?*', async route => {
        const url = new URL(route.request().url());
        requested.push(url.searchParams.get('before'));
        const current = url.pathname.includes(other.external_id) ? other : task;
        if (mode === 'hold' && current === task) await new Promise(resolve => { release = resolve; });
        if (mode === 'error' || (url.searchParams.has('before') && failMore)) {
            return route.fulfill({ status: 502, json: { message: 'safe failure' } });
        }
        const ids = current === other ? [99] : mode === 'fresh' ? [50] : url.searchParams.has('before') ? [10] : Array.from({ length: 20 }, (_, i) => 30 - i);
        await route.fulfill({ json: { workspace_id: workspace.external_id, task_id: current.external_id,
            items: ids.map(item), next_cursor: ids.length === 20 ? '11' : null } }).catch(() => {});
    });
    await area().getByRole('button', { name: '刷新', exact: true }).click();
    await waitCount(20);
    assert.ok((await area().innerText()).includes('未知状态：future-state'));
    assert.ok((await area().innerText()).includes('耗时：0 ms'));
    await area().getByRole('button', { name: '加载更多' }).click();
    await area().getByRole('alert').waitFor();
    assert.equal(await area().locator('li').count(), 20);
    failMore = false;
    await area().getByRole('button', { name: '重试' }).click();
    await waitCount(21);
    assert.deepEqual(requested.slice(-2), ['11', '11']);
    console.log('PASS pagination failure preserves rows and retries same cursor');
    mode = 'error';
    await area().getByRole('button', { name: '刷新', exact: true }).click();
    await area().getByRole('alert').waitFor();
    assert.equal(await area().locator('li').count(), 21);
    mode = 'fresh';
    await page.evaluate(() => {
        window.historySkeletonSeen = false;
        window.historyObserver = new MutationObserver(records => {
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (node instanceof Element && (node.matches('[aria-label="正在读取运行历史"]') || node.querySelector('[aria-label="正在读取运行历史"]'))) {
                        window.historySkeletonSeen = true;
                    }
                }
            }
        });
        window.historyObserver.observe(document.body, { childList: true, subtree: true });
    });
    await area().getByRole('button', { name: '重试' }).click();
    await waitCount(1);
    assert.ok((await area().innerText()).includes('#50'));
    assert.equal(await page.evaluate(() => { window.historyObserver.disconnect(); return window.historySkeletonSeen; }), false);
    console.log('PASS refresh preserves rows on failure and fast response has no skeleton');

    mode = 'hold';
    await area().getByRole('button', { name: '刷新', exact: true }).click();
    await area().getByRole('status', { name: '正在读取运行历史' }).waitFor();
    assert.ok((await area().innerText()).includes('#50'));
    await page.getByRole('button', { name: other.title, exact: true }).click();
    await area().getByText('#99', { exact: true }).waitFor();
    release();
    await page.waitForTimeout(350);
    assert.equal(await area().locator('li').count(), 1);
    assert.ok(!(await area().innerText()).includes('#50'));
    console.log('PASS delayed skeleton and task switch isolates late response');
    await page.getByRole('button', { name: '收起详情', exact: true }).click();
    assert.equal(await area().count(), 0);
    const count = requested.length;
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await area().getByText('#99', { exact: true }).waitFor();
    // React 开发模式可能重跑挂载 effect；只约束每次都重新请求第一页。
    assert.ok(requested.length > count);
    assert.ok(requested.slice(count).every(cursor => cursor === null));
    console.log('PASS reopen reloads first page');
    await mkdir('/private/tmp/agent-ui-preview/output/playwright', { recursive: true });
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await page.screenshot({ path: `/private/tmp/agent-ui-preview/output/playwright/run-history-${width}.png` });
    }
    // 详情保持独立状态；列表节点与聊天草稿不因查看历史而重建。
    const detailArea = () => page.getByRole('region', { name: '历史运行详情', exact: true });
    let detailMode = 'error';
    let releaseDetail;
    const listNode = await area().elementHandle();
    await page.getByLabel('你的问题').fill('保留当前草稿');
    await page.route('**/api/runs/*', async route => {
        if (detailMode === 'hold') await new Promise(resolve => { releaseDetail = resolve; });
        if (detailMode === 'error') return route.fulfill({ status: 502, json: { message: 'failed' } });
        const id = Number(new URL(route.request().url()).pathname.split('/').at(-1));
        const events = detailMode === 'empty' ? [] : [
            { id: 1, event_type: 'TEXT_MESSAGE_CONTENT', created_at: '2026-09-16T00:00:00Z', payload: { chunk: '<img src=x onerror="window.injected=true">' } },
            { id: 2, event_type: 'FUTURE_EVENT', created_at: '2026-09-16T00:00:01Z', payload: { secret: 'PRIVATE' } },
            { id: 3, event_type: 'RUN_ERROR', created_at: '2026-09-16T00:00:02Z', payload: { reason: 'timeout' } },
        ];
        await route.fulfill({ json: { ...item(id), events } }).catch(() => {});
    });
    await area().getByRole('button', { name: '查看运行 99' }).click();
    await detailArea().getByRole('alert').waitFor();
    detailMode = 'success';
    await detailArea().getByRole('button', { name: '重试详情' }).click();
    await detailArea().getByText('运行超时', { exact: true }).waitFor();
    assert.ok((await detailArea().innerText()).includes('未知事件：FUTURE_EVENT'));
    assert.ok(!(await detailArea().innerText()).includes('PRIVATE'));
    assert.equal(await detailArea().locator('img').count(), 0);
    assert.equal(await page.getByLabel('你的问题').inputValue(), '保留当前草稿');
    assert.equal(await page.evaluate(() => window.injected), undefined);
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await page.screenshot({ path: `/private/tmp/agent-ui-preview/output/playwright/run-detail-${width}.png` });
    }
    await detailArea().getByRole('button', { name: '返回列表' }).click();
    assert.equal(await listNode.evaluate(node => node === document.querySelector('[aria-label="历史运行"]')), true);
    assert.equal(await area().locator('li').count(), 1);
    console.log('PASS detail retry, unknown payload filtering, text escaping, draft/list preserved');

    detailMode = 'hold';
    await area().getByRole('button', { name: '查看运行 99' }).click();
    await detailArea().getByRole('status', { name: '正在读取历史运行详情' }).waitFor();
    await detailArea().getByRole('button', { name: '返回列表' }).click();
    detailMode = 'empty';
    releaseDetail();
    await area().getByRole('button', { name: '查看运行 99' }).click();
    await detailArea().getByText('这次运行没有已记录的事件。').waitFor();
    assert.equal(await detailArea().locator('ol').count(), 0);
    console.log('PASS return cancels late detail and fresh empty detail renders');
    await detailArea().getByRole('button', { name: '返回列表' }).click();
    detailMode = 'hold';
    await area().getByRole('button', { name: '查看运行 99' }).click();
    await detailArea().getByRole('status', { name: '正在读取历史运行详情' }).waitFor();
    mode = 'fresh';
    await page.getByRole('button', { name: task.title, exact: true }).click();
    await area().getByRole('button', { name: '查看运行 50' }).waitFor();
    detailMode = 'success';
    releaseDetail();
    await page.waitForTimeout(300);
    assert.equal(await detailArea().count(), 0);
    await area().getByRole('button', { name: '查看运行 50' }).click();
    await detailArea().getByText('运行超时', { exact: true }).waitFor();
    await page.getByRole('button', { name: '收起详情', exact: true }).click();
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await area().getByRole('button', { name: '查看运行 50' }).waitFor();
    assert.equal(await detailArea().count(), 0);
    console.log('PASS task switch isolates detail; reopen returns to list');
    // 保留真实聊天链路，只替换流中的指标用于复现用户截图的长数值。
    await page.route('**/api/chat/stream', async route => {
        const response = await route.fetch();
        const body = (await response.text()).split('\n').map(line => {
            if (!line.trim()) return line;
            const event = JSON.parse(line);
            if (event.type === 'RUN_FINISHED') {
                event.metrics.model_usage = { input_tokens: 700, output_tokens: 57, total_tokens: 757,
                    cache_hit_input_tokens: null, cache_miss_input_tokens: null };
                event.metrics.model_duration_ms = 1499;
                event.metrics.estimated_cost_cny = '0.00128420';
            }
            return JSON.stringify(event);
        }).join('\n');
        await route.fulfill({ response, body });
    });
    await page.setViewportSize({ width: 1366, height: 900 });
    // 通过真实聊天/BFF/隔离数据库生成摘要，模型出口由测试服务模拟。
    await page.getByLabel('你的问题').fill('请简单介绍一下这个项目');
    await page.getByLabel('你的问题').press('Enter');
    const summary = page.getByRole('region', { name: '运行摘要', exact: true });
    await summary.waitFor();
    assert.ok((await summary.innerText()).includes('757 Token'));
    assert.ok((await summary.innerText()).includes('¥0.00128420'));
    await page.getByLabel('你的问题').fill('拖动时保留草稿');
    const inputNode = await page.getByLabel('你的问题').elementHandle();
    const separator = page.getByRole('separator', { name: '调整运行详情宽度' });
    await separator.focus();
    await page.keyboard.press('Home');
    assert.equal(await separator.getAttribute('aria-valuenow'), '320');
    assert.ok(await summary.locator('dd').evaluateAll(nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1)));
    await page.screenshot({ path: '/private/tmp/agent-ui-preview/output/playwright/details-narrow.png' });
    const bounds = await separator.boundingBox();
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 120);
    await page.mouse.down();
    await page.mouse.move(bounds.x - 220, bounds.y + 120, { steps: 8 });
    await page.mouse.up();
    const draggedWidth = Number(await separator.getAttribute('aria-valuenow'));
    assert.ok(draggedWidth >= 530 && draggedWidth <= 550);
    assert.equal(await page.getByLabel('你的问题').inputValue(), '拖动时保留草稿');
    assert.equal(await inputNode.evaluate(node => node === document.querySelector('textarea')), true);
    await separator.focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(Number(await separator.getAttribute('aria-valuenow')), draggedWidth - 16);
    await page.screenshot({ path: '/private/tmp/agent-ui-preview/output/playwright/details-wide.png' });
    await page.reload();
    await page.getByLabel('你的问题').waitFor();
    await page.getByRole('button', { name: '展开详情', exact: true }).click();
    await page.waitForFunction(width => document.querySelector('[role="separator"]')?.getAttribute('aria-valuenow') === String(width), draggedWidth - 16);
    console.log('PASS real run summary, narrow metrics, pointer/keyboard resize, draft/node preservation and persisted width');
    assert.deepEqual(errors, []);
} finally {
    await context.close();
    await browser.close();
}
