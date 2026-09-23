import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../output/playwright/sample-status-component');
    await mkdir(output, { recursive: true });
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    const checks = [];

    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let requests = 0;
        let writes = 0;
        let mode = 'missing';
        let release;
        let started;
        let entered;

        await page.route('**/api/workspaces/**/sample-status', async route => {
            requests++;
            const request = route.request();
            if (request.method() !== 'GET') writes++;
            assert.equal(request.method(), 'GET');
            assert.equal(request.postData(), null);
            assert.equal(new URL(request.url()).search, '');
            if (mode === 'delayed') {
                entered?.();
                await new Promise(resolve => { release = resolve; });
            }
            if (mode === 'network') { await route.abort().catch(() => {}); return; }
            if (mode === 'invalid-json') { await route.fulfill({ status: 200, body: '{PRIVATE' }).catch(() => {}); return; }
            if (mode === '404') { await route.fulfill({ status: 404, body: 'PRIVATE' }).catch(() => {}); return; }
            const taskId = new URL(request.url()).pathname.split('/').at(-2);
            const body = {
                workspace_id: 'a'.repeat(32), task_id: mode === 'mismatch' ? 'f'.repeat(32) : taskId,
                status: mode === 'invalid-status' ? 'unknown' :
                    ['sealed', 'delayed', 'cleanup-pending', 'invalid-reason', 'missing-reason'].includes(mode)
                        ? 'sealed' : mode === 'invalid-combo' ? 'ready' : mode,
                sealed_reason: mode === 'cleanup-pending' || mode === 'invalid-combo' ? 'cleanup_pending' :
                    mode === 'invalid-reason' ? 'PRIVATE' :
                        ['sealed', 'delayed', 'missing-reason'].includes(mode) ? 'unavailable' : null,
                sample_handle: 'PRIVATE',
            };
            if (mode === 'missing-reason') delete body.sealed_reason;
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
        });

        await page.goto(base);
        const panel = () => page.getByRole('region', { name: '受限样例登记状态', exact: true });
        const query = () => panel().getByRole('button', { name: /查询登记状态/ });
        await query().waitFor();
        assert.equal(requests, 0);
        assert.ok((await panel().innerText()).includes('尚未查询'));

        await query().focus();
        await page.keyboard.press('Enter');
        await panel().getByText('查询时没有该任务的进程内样例登记').waitFor();
        assert.equal(requests, 1);
        checks.push('manual keyboard GET and missing snapshot');

        for (const [status, label] of [
            ['busy', '查询时样例正在使用'],
            ['sealed', '查询时登记已封锁或绑定不匹配'],
            ['ready', '查询时登记可供后续门禁检查'],
        ]) {
            mode = status;
            await query().click();
            await panel().getByText(label).waitFor();
        }
        assert.ok((await panel().innerText()).includes('此状态不预留执行权'));
        checks.push('four states and ready limitation');

        mode = 'cleanup-pending';
        await query().click();
        await panel().getByText('查询时样例登记处于清理待办').waitFor();
        const pendingText = await panel().innerText();
        assert.ok(pendingText.includes('持久化登记的清理待办状态'));
        assert.ok(pendingText.includes('不能据此判断样例目录是否仍存在'));
        assert.ok(!pendingText.includes('PRIVATE'));
        assert.equal(await panel().getByRole('button').count(), 1);
        checks.push('cleanup-pending reason is visible without private details or action');

        for (const failure of ['invalid-status', 'invalid-reason', 'missing-reason', 'invalid-combo', 'mismatch', 'invalid-json', '404', 'network']) {
            mode = failure;
            await query().click();
            await panel().getByRole('alert').waitFor();
            const text = await panel().innerText();
            assert.ok(text.includes('当前状态未知'));
            assert.ok(!text.includes('PRIVATE'));
            assert.ok(!text.includes('查询时登记可供后续门禁检查'));
            assert.ok(!text.includes('查询时样例登记处于清理待办'));
        }
        checks.push('invalid, mismatched, HTTP and network failures');

        mode = 'delayed';
        started = new Promise(resolve => { entered = resolve; });
        await query().click();
        await started;
        await panel().getByRole('button', { name: '取消查询' }).click();
        release();
        release = undefined;
        await panel().getByText('尚未查询当前任务的样例登记。').waitFor();
        assert.ok(!(await panel().innerText()).includes('登记已封锁'));
        checks.push('cancel ignores delayed response');

        started = new Promise(resolve => { entered = resolve; });
        await query().click();
        await started;
        await page.getByRole('button', { name: '任务 B' }).click();
        release();
        release = undefined;
        await panel().getByText('尚未查询当前任务的样例登记。').waitFor();
        mode = 'ready';
        await query().click();
        await panel().getByText('查询时登记可供后续门禁检查').waitFor();
        checks.push('task switch excludes old snapshot');

        mode = 'delayed';
        started = new Promise(resolve => { entered = resolve; });
        await query().click();
        await started;
        await page.getByRole('button', { name: '关闭详情' }).click();
        release();
        release = undefined;
        await page.getByRole('button', { name: '打开详情' }).click();
        await panel().getByText('尚未查询当前任务的样例登记。').waitFor();
        checks.push('unmount ignores delayed response');

        await page.getByRole('button', { name: '无效任务' }).click();
        assert.ok(await query().isDisabled());
        assert.ok((await panel().innerText()).includes('任务信息不完整'));
        await page.getByRole('button', { name: '任务 A' }).click();

        mode = 'cleanup-pending';
        await query().click();
        await panel().getByText('查询时样例登记处于清理待办').waitFor();

        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await panel().scrollIntoViewIfNeeded();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.screenshot({ path: path.join(output, `sample-status-${width}.png`), fullPage: true });
        }
        assert.equal(writes, 0);
        assert.deepEqual(errors, []);
        await writeFile(path.join(output, 'evidence.json'), JSON.stringify({ checks, requests, writes, widths: [1366, 1920], pageErrors: errors }, null, 2));
        console.log(`PASS sample status component: ${checks.join(', ')}`);
    } finally {
        await browser.close();
    }
}
