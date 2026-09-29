import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixtureOutput = fileURLToPath(new URL('../../output/playwright/project-write-grant-integration/', import.meta.url));
const fixtures = JSON.parse(await readFile(fixtureOutput + 'fixtures.json', 'utf8'));
const directory = process.env.BROWSER_TRACE_DIRECTORY;
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(directory && base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const events = [], grants = {};
let sequence = 0;
async function call(item, suffix, method, body, expected, action) {
    const id = `request-${++sequence}`;
    const path = `/api/workspaces/${item.workspace_id}/tasks/${item.task_id}/file-edit-proposals/${item.proposal_id}${suffix}`;
    // 真浏览器同源fetch；每个写动作仅发送一次，失败立即停止，不自动重放。
    let response;
    if (action) {
        await page.route(base + path, async route => {
            assert.deepEqual(route.request().postDataJSON(), body);
            await route.continue({ headers: { ...route.request().headers(), "x-isolated-trace-id": id } });
        }, { times: 1 });
        const pending = page.waitForResponse(res => res.url() === base + path && res.request().method() === method);
        await action();
        const res = await pending;
        const text = await res.text();
        let data;
        try { data = JSON.parse(text); } catch { data = { non_json: true, prefix: text.slice(0, 120) }; }
        response = { status: res.status(), content_type: res.headers()["content-type"], data };
    } else response = await page.evaluate(async ({ id, path, method, body }) => {
        const res = await fetch(path, {
            method, headers: { 'Content-Type': 'application/json', 'X-Isolated-Trace-Id': id },
            ...(body ? { body: JSON.stringify(body) } : {}), cache: 'no-store', redirect: 'error',
        });
        const text = await res.text();
        let data;
        try { data = JSON.parse(text); } catch { data = { non_json: true, prefix: text.slice(0, 120) }; }
        return { status: res.status, content_type: res.headers.get('content-type'), data };
    }, { id, path, method, body });
    events.push({ id, path, method, expected, ...response });
    await writeFile(`${directory}/browser.json`, JSON.stringify(events, null, 4));
    assert.equal(response.status, expected, JSON.stringify(events.at(-1)));
    return response.data;
}
try {
    await page.goto(base);
    const [normal, unknown, foreign] = fixtures;
    for (const item of [normal, unknown]) {
        const ui = process.env.BROWSER_TRACE_UI === '1';
        if (ui) {
            await page.goto(`${base}/?workspace=${item.workspace_id}&task=${item.task_id}`);
            await page.getByRole('button', { name: '查看改动', exact: true }).click();
            const pane = page.getByRole('complementary', { name: '文件改动', exact: true });
            await pane.locator('summary').filter({ hasText: 'example.txt' }).click();
            await pane.getByRole('button', { name: '查看提案详情', exact: true }).click();
            await pane.locator('summary').filter({ hasText: '普通项目写入许可' }).click();
        }
        await call(item, '', 'GET', null, 200);
        await call(item, '/decision', 'POST', { decision: 'approved' }, 200,
            ui ? () => page.getByRole('button', { name: '批准提案', exact: true }).press('Enter') : undefined);
        if (ui) {
            await page.getByText('已确认批准此提案。应用状态以执行结果为准。', { exact: true }).waitFor();
            await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
            await page.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        }
        const receipt = await call(item, '/write-grant', 'POST', { action: 'grant' }, 201,
            ui ? () => page.getByRole('button', { name: '确认发放许可', exact: true }).click() : undefined);
        grants[item.marker] = receipt.grant;
        const stale = await call(item, '/write-grant/revoke', 'POST', { grant_id: receipt.grant.grant_id, revision: 2 }, 409);
        assert.equal(stale.code, 'project_write_grant_conflict');
        if (item === normal) {
            if (ui) await page.getByRole('button', { name: '撤销本提案许可', exact: true }).click();
            await call(item, '/write-grant/revoke', 'POST', { grant_id: receipt.grant.grant_id, revision: 1 }, 200,
                ui ? () => page.getByRole('button', { name: '确认撤销许可', exact: true }).press('Enter') : undefined);
        }
    }
    const denied = await call(foreign, '/decision', 'POST', { decision: 'approved' }, 404);
    assert.equal(denied.code, 'workspace_not_accessible');
    // 只核对所跟踪动作；详情GET用于确认资源存在，不经过诊断封装。
    const readEvents = async name => (await readFile(`${directory}/${name}.jsonl`, 'utf8')).trim().split('\n').map(JSON.parse);
    let api = [], bff = [];
    const writeIds = events.filter(event => event.method === 'POST').map(event => event.id);
    // API可先发送响应再完成只读归属取证；只等待日志，不重放HTTP写请求。
    for (let attempt = 0; attempt < 100; attempt++) {
        try { api = await readEvents('api'); bff = await readEvents('bff'); } catch { /* 日志可能尚未创建或正在追加。 */ }
        if (writeIds.every(id => api.some(row => row.id === id) && bff.some(row => row.id === id))) break;
        await delay(50);
    }
    for (const event of events.filter(event => event.method === 'POST')) {
        const backend = api.filter(row => row.id === event.id);
        const proxy = bff.filter(row => row.id === event.id);
        assert.equal(backend.length, 1); assert.equal(proxy.length, 1);
        assert.equal(backend[0].status, event.status); assert.equal(proxy[0].status, event.status);
        assert.equal(proxy[0].backend_run, backend[0].run);
    }
    const deniedTrace = api.find(row => row.id === events.at(-1).id);
    assert.deepEqual(deniedTrace.ownership_snapshot, {
        workspace_exists: true, workspace_owned: false, task_linked: true, proposal_linked: true,
    });
    await writeFile(fixtureOutput + 'browser.json', JSON.stringify(grants, null, 4));
    await writeFile(`${directory}/result.json`, JSON.stringify({ completed: true, ui: process.env.BROWSER_TRACE_UI === '1', requests: events.length,
        traced_writes: events.filter(row => row.method === 'POST').length, unexpected_404: false,
        boundary: 'browser same-origin fetch; real BFF/API; cold temporary Next; no automatic write retries',
    }, null, 4));
    console.log(`PASS correlated cold requests: ${directory}`);
} catch (error) {
    const readTrace = async name => {
        try { return (await readFile(`${directory}/${name}.jsonl`, 'utf8')).trim().split('\n').map(JSON.parse); }
        catch { return []; }
    };
    const last = events.at(-1);
    const api = (await readTrace('api')).filter(row => row.id === last?.id);
    const bff = (await readTrace('bff')).filter(row => row.id === last?.id);
    // 缺少日志只表示证据缺口；不把它直接解释为某层已经有缺陷。
    const layerHint = api.length ? 'backend response observed'
        : bff.length ? 'upstream response without matching backend trace'
            : 'no matching upstream trace; inspect browser response and Next routing';
    await writeFile(`${directory}/result.json`, JSON.stringify({ completed: false, error: String(error),
        requests: events.length, last, api, bff, layer_hint: layerHint,
    }, null, 4));
    throw error;
} finally { await browser.close(); }
