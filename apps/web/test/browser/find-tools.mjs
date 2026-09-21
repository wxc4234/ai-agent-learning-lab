import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, readFile, writeFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Use local isolated launcher');
const output = '/private/tmp/agent-ui-preview/output/playwright';
await mkdir(output, { recursive: true });
const fixture = await mkdtemp('/private/tmp/agent-find-');
const root = `${await realpath(fixture)}/project`;
await mkdir(root);
const content = 'answer-from-nested-real-file\n';
await mkdir(`${root}/src/nested`, { recursive: true });
await mkdir(`${root}/many`);
await writeFile(`${root}/src/nested/定位.txt`, content);
for (let i = 0; i < 51; i++) await writeFile(`${root}/many/bulk-${i}.txt`, 'fixture');
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
    for (const [marker, bound, answer, code] of [
        ['success', true, '读取 src/nested/定位.txt：answer-from-nested-real-file', null],
        ['empty', true, '当前查找范围内没有匹配文件', null],
        ['truncated', true, '查找未完整覆盖，已返回50个文件', null],
        ['escape', true, '查找失败：路径不存在、不可访问或不符合项目路径规则', 'workspace_path_rejected'],
        ['unbound', false, '查找失败：当前项目尚未绑定本地目录', 'workspace_directory_unbound'],
    ]) {
        const { workspace, task } = await setup(`文件查找-${marker}`, bound);
        const { events, runId } = await send(`[find-${marker}] 查找并分析文件`, answer);
        assert.deepEqual(events.filter(e => e.type === 'TOOL_CALL_START').map(e => e.tool_name),
            marker === 'success' ? ['find_files', 'read_text_file'] : ['find_files']);
        const results = events.filter(e => e.type === 'TOOL_CALL_RESULT');
        const failures = events.filter(e => e.type === 'TOOL_CALL_ERROR');
        if (code) {
            assert.equal(results.length, 0);
            assert.equal(failures.length, 1);
            assert.equal(failures[0].details, code);
        } else {
            assert.equal(failures.length, 0);
            assert.equal(results.length, marker === 'success' ? 2 : 1);
            const found = JSON.parse(results[0].result);
            assert.equal(found.truncated, marker === 'truncated');
            assert.ok(found.scanned_entries > 0);
            if (marker === 'success') {
                assert.deepEqual(found.paths, ['src/nested/定位.txt']);
                assert.equal(JSON.parse(results[1].result).content, content);
            } else if (marker === 'empty') assert.deepEqual(found.paths, []);
            else {
                assert.equal(found.paths.length, 50);
                assert.equal(found.scanned_entries, 51);
                assert.ok(found.paths.every(path => path.startsWith('many/bulk-')));
            }
        }
        await page.getByText('find_files', { exact: true }).waitFor();
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/find-${marker}-${width}.png` });
        }
        const body = await page.locator('body').innerText();
        assert.ok(!body.includes('OUTSIDE_SECRET_NEVER_RETURN'));
        assert.ok(!body.includes(root));
        assert.ok(!JSON.stringify(events).includes(root));
        const before = posts;
        await page.reload();
        await page.getByText(answer, { exact: true }).waitFor();
        assert.equal(posts, before, 'reload must not execute tools again');
        const history = await page.request.get(`${base}/api/workspaces/${workspace.external_id}/tasks/${task.external_id}/messages`);
        assert.equal(history.status(), 200);
        assert.ok((await history.text()).includes(answer));
        const persisted = await page.request.get(`${base}/api/runs/${runId}`);
        assert.equal(persisted.status(), 200);
        const run = await persisted.json();
        assert.equal(run.status, 'done');
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_RESULT').length, results.length);
        assert.equal(run.events.filter(e => e.event_type === 'TOOL_CALL_ERROR').length, failures.length);
        console.log(`PASS ${marker}: keyboard/BFF/real files/PC widths/persistence/no replay`);
    }
    assert.equal(await readFile(`${root}/src/nested/定位.txt`, 'utf8'), content);
    assert.equal(await readFile(`${fixture}/outside.txt`, 'utf8'), 'OUTSIDE_SECRET_NEVER_RETURN');
    assert.deepEqual(errors, []);
} catch (error) {
    await page.screenshot({ path: `${output}/find-failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
    await rm(fixture, { recursive: true, force: true });
    console.log('Temporary find fixture removed.');
}
