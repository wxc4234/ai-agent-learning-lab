import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const fixture = await mkdtemp('/private/tmp/agent-readonly-');
const root = `${await realpath(fixture)}/project`;
await mkdir(root);
const content = '说明：只读样例\nTARGET: answer-from-real-file\n末尾上下文\n';
await writeFile(`${root}/定位样例.txt`, content);
await writeFile(`${fixture}/outside.txt`, 'OUTSIDE_SECRET_NEVER_RETURN');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let posts = 0;
page.on('request', request => {
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
try {
    const { workspace, task } = await setup('只读工具链验收', true);
    const answer = '定位到 定位样例.txt 第 2 行：TARGET: answer-from-real-file';
    const { events, runId } = await send('[readonly-success] 找到 TARGET 并读取上下文', answer);
    assert.deepEqual(events.filter(event => event.type === 'TOOL_CALL_START').map(event => event.tool_name), ['list_directory', 'search_text_file', 'read_text_file']);
    assert.equal(events.filter(event => event.type === 'TOOL_CALL_RESULT').length, 3);
    for (const name of ['list_directory', 'search_text_file', 'read_text_file']) {
        await page.getByText(name, { exact: true }).waitFor();
    }
    const searchEvent = events.filter(event => event.type === 'TOOL_CALL_RESULT')[1];
    const search = JSON.parse(searchEvent.result);
    assert.equal(search.matches[0].line_number, 2);
    assert.equal(search.truncated, false);
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: `${output}/readonly-tools-${width}.png` });
    }
    console.log('PASS browser keyboard → BFF → real directory/search/read → answer, 1366/1920 PC');
    await page.reload();
    await page.getByText(answer, { exact: true }).waitFor();
    assert.equal(posts, 1, 'reload must not execute tools again');
    const history = await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/messages`);
    assert.equal(history.status(), 200);
    assert.ok((await history.text()).includes(answer));
    const run = await page.request.get(`${base}/api/runs/${runId}`);
    assert.equal(run.status(), 200);
    const detail = await run.json();
    assert.equal(detail.status, 'done');
    assert.equal(detail.events.filter(event => event.event_type === 'TOOL_CALL_RESULT').length, 3);
    console.log('PASS persisted answer and three tool events; reload has no new chat POST');
    for (const [name, bound, marker, code, message] of [
        ['越界读取验收', true, 'escape', 'workspace_path_rejected', '路径不存在、不可访问或不符合项目路径规则'],
        ['未绑定项目验收', false, 'unbound', 'workspace_directory_unbound', '当前项目尚未绑定本地目录'],
    ]) {
        await setup(name, bound);
        const failed = await send(`[readonly-${marker}] 检查只读边界`, `只读检查失败：${message}`);
        const toolErrors = failed.events.filter(event => event.type === 'TOOL_CALL_ERROR');
        assert.equal(toolErrors.length, 1);
        assert.equal(toolErrors[0].details, code);
        assert.equal(failed.events.filter(event => event.type === 'TOOL_CALL_RESULT').length, 0);
        assert.ok(!(await page.locator('body').innerText()).includes('OUTSIDE_SECRET_NEVER_RETURN'));
        assert.ok(!JSON.stringify(failed.events).includes(root));
        await page.getByText(`错误：${message}`, { exact: true }).waitFor();
        await page.screenshot({ path: `${output}/readonly-${marker}.png` });
        console.log(`PASS ${marker}: safe tool error visible, model can explain and finish`);
    }
    assert.equal(await readFile(`${root}/定位样例.txt`, 'utf8'), content);
    assert.equal(await readFile(`${fixture}/outside.txt`, 'utf8'), 'OUTSIDE_SECRET_NEVER_RETURN');
    assert.deepEqual(errors, []);
} catch (error) {
    await page.screenshot({ path: `${output}/readonly-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
}
