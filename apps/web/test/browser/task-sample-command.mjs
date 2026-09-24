import assert from 'node:assert/strict';
import { access, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const fixtures = JSON.parse(await readFile(process.env.TASK_SAMPLE_FIXTURE, 'utf8'));
const output = process.env.TASK_SAMPLE_OUTPUT;
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
let posts = 0;
const errors = [];
const evidence = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) posts++;
});
async function waitUntil(read, message) {
    for (let attempt = 0; attempt < 150; attempt++) {
        const result = await read();
        if (result) return result;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error(message);
}
async function runDetail(id, status) {
    return waitUntil(async () => {
        const response = await page.request.get(`${base}/api/runs/${id}`);
        assert.equal(response.status(), 200);
        const body = await response.json();
        return body.status === status ? body : null;
    }, `run ${id} did not reach ${status}`);
}
try {
    for (const fixture of fixtures) {
        const { mode, workspace_id: workspace, task_id: task } = fixture;
        await page.goto(`${base}/?workspace=${workspace}&task=${task}`);
        await page.getByLabel('你的问题').waitFor();
        await page.screenshot({ path: `${output}/${mode}-before.png` });
        const statusUrl = `${base}/api/workspaces/${workspace}/tasks/${task}/sample-status`;
        assert.equal((await (await page.request.get(statusUrl)).json()).status, 'ready');
        await page.getByLabel('你的问题').fill(`[task-sample-${mode}] 执行样例只读命令`);
        const pending = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'));
        await page.getByLabel('你的问题').press('Enter');
        const response = await pending;
        assert.equal(response.status(), 200);
        const id = response.headers()['x-run-id'];
        assert.ok(id);
        if (mode === 'cancel') {
            // 夹具只观察真实 attach 字节，不用固定 sleep 猜测容器是否已启动。
            await waitUntil(async () => access(process.env.TASK_SAMPLE_READY).then(() => true, () => false), 'container readiness');
            const cancelled = page.waitForResponse(r => /\/api\/runs\/\d+\/cancel$/.test(r.url()));
            await page.getByRole('button', { name: '停止生成', exact: true }).click();
            assert.equal((await cancelled).status(), 204);
        } else {
            const answer = mode === 'cleanup'
                ? '样例命令错误：command_cleanup_unconfirmed'
                : `样例命令完成：退出码${mode === 'nonzero' ? 7 : 0}`;
            await page.getByText(answer, { exact: true }).waitFor();
        }
        const terminal = mode === 'cancel' ? 'aborted' : 'done';
        const run = await runDetail(id, terminal);
        const results = run.events.filter(event => event.event_type === 'TOOL_CALL_RESULT');
        const failures = run.events.filter(event => event.event_type === 'TOOL_CALL_ERROR');
        const starts = run.events.filter(event => event.event_type === 'TOOL_CALL_START');
        assert.equal(starts.length, 1);
        const expand = page.getByRole('button', { name: '展开详情', exact: true });
        if (await expand.isVisible()) await expand.click();
        await page.getByText('run_command', { exact: true }).waitFor();
        if (mode === 'success' || mode === 'nonzero') {
            assert.equal(results.length, 1);
            assert.equal(failures.length, 0);
            const result = JSON.parse(results[0].payload.result);
            assert.equal(result.exit_code, mode === 'nonzero' ? 7 : 0);
            assert.equal(result.stdout, 'TASK_SAMPLE_READONLY_OK\n');
            assert.equal(result.stderr, '');
            assert.equal(result.oom_killed, false);
            assert.equal(result.daemon_error, false);
            assert.equal(result.stdout_truncated, false);
            await page.getByText(mode === 'success' ? '命令成功' : '命令失败', { exact: true }).waitFor();
            const stdout = page.getByLabel('stdout', { exact: true });
            assert.equal(await stdout.textContent(), result.stdout);
            await stdout.focus();
            assert.ok(await stdout.evaluate(element => element === document.activeElement));
        } else {
            assert.equal(results.length, 0, 'failure/cancellation must not invent success');
            if (mode === 'cleanup') {
                assert.equal(failures.length, 1);
                assert.equal(failures[0].payload.details, 'command_cleanup_unconfirmed');
            } else {
                await page.getByText('结果未确认', { exact: true }).waitFor();
            }
        }
        for (const key of ['execution_token', 'container_id', 'sample_root', 'recovery_journal']) {
            assert.ok(!JSON.stringify(run.events).includes(key), `private field leaked: ${key}`);
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/${mode}-${width}.png` });
        }
        const before = posts;
        await page.reload();
        await page.getByLabel('你的问题').waitFor();
        const history = await runDetail(id, terminal);
        assert.deepEqual(history.events, run.events);
        assert.equal(posts, before, 'reload cannot replay a command');
        assert.equal((await (await page.request.get(statusUrl)).json()).status, 'ready');
        evidence.push({ mode, run_id: Number(id), status: terminal, posts, event_count: history.events.length, reload_no_replay: true });
        console.log(`PASS ${mode}: PC keyboard -> BFF -> API -> Task snapshot -> real Docker; persistence/reload`);
    }
    assert.equal(posts, 4);
    assert.deepEqual(errors, []);
    await writeFile(`${output}/browser-evidence.json`, JSON.stringify(evidence, null, 4));
} catch (error) {
    console.error('Browser errors:', errors);
    console.error(await page.locator('body').innerText({ timeout: 5000 }).catch(() => 'DOM unavailable'));
    await page.screenshot({ path: `${output}/failure.png`, timeout: 5000 }).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
