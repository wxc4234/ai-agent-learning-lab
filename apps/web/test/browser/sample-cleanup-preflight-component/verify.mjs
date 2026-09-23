import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const classifications = [
    ['evidence_missing', '缺少来源证据'],
    ['not_pending', '不处于清理待办'],
    ['evidence_inconsistent', '证据不一致'],
    ['directory_missing', '目录不存在'],
    ['identity_unverifiable', '身份无法核实'],
    ['identity_matches_record', '身份与记录一致'],
    ['inspection_unavailable', '检查暂不可用'],
];

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../output/playwright/sample-cleanup-preflight-component');
    await mkdir(output, { recursive: true });
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    const checks = [];

    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let requests = 0;
        let writes = 0;
        let mode = 'evidence_missing';
        let release;
        let entered;
        let finished;

        await page.route('**/api/workspaces/**/sample-cleanup-preflight', async route => {
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
                workspace_id: 'a'.repeat(32),
                task_id: mode === 'mismatch' ? 'f'.repeat(32) : taskId,
                result: mode === 'invalid-result' ? 'PRIVATE' : mode === 'delayed' ? 'identity_matches_record' : mode,
                root_path: '/PRIVATE/workspace',
                root_dev: 123,
                root_ino: 456,
                sample_handle: 'PRIVATE',
            };
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
            if (mode === 'delayed') finished?.();
        });

        await page.goto(base);
        const panel = () => page.getByRole('region', { name: '清理待办只读诊断', exact: true });
        const query = () => panel().getByRole('button', { name: /查询清理诊断/ });
        await query().waitFor();
        assert.equal(requests, 0);
        assert.ok((await panel().innerText()).includes('尚未查询'));

        await query().focus();
        await page.keyboard.press('Enter');
        await panel().getByText('缺少来源证据', { exact: false }).waitFor();
        assert.equal(requests, 1);
        checks.push('manual keyboard GET and idle state without auto-read');

        for (const [result, label] of classifications.slice(1)) {
            mode = result;
            await query().click();
            await panel().getByText(label, { exact: false }).waitFor();
            const visible = await panel().innerText();
            assert.ok(!visible.includes('PRIVATE'));
            assert.ok(!visible.includes('/PRIVATE/workspace'));
            if (result === 'identity_matches_record') {
                assert.ok(visible.includes('不能据此清理'));
            }
        }
        assert.equal(await panel().getByRole('button', { name: /执行清理|恢复登记|重试应用|重新应用/ }).count(), 0);
        checks.push('seven fixed classifications, redaction and no mutation controls');

        for (const failure of ['invalid-result', 'mismatch', 'invalid-json', '404', 'network']) {
            mode = failure;
            await query().click();
            await panel().getByRole('alert').waitFor();
            const visible = await panel().innerText();
            assert.ok(visible.includes('诊断查询失败，当前目录状态未知'));
            assert.ok(!visible.includes('PRIVATE'));
            assert.ok(!visible.includes('身份与记录一致'));
        }
        checks.push('invalid, mismatched, HTTP and network responses stay unknown');

        mode = 'delayed';
        let started = new Promise(resolve => { entered = resolve; });
        let settled = new Promise(resolve => { finished = resolve; });
        await query().click();
        await started;
        await panel().getByRole('button', { name: '取消查询' }).click();
        release();
        release = undefined;
        await settled;
        await page.waitForTimeout(100);
        await panel().getByText('尚未查询', { exact: false }).waitFor();
        assert.ok(!(await panel().innerText()).includes('身份与记录一致'));
        checks.push('manual cancellation ignores delayed response');

        started = new Promise(resolve => { entered = resolve; });
        settled = new Promise(resolve => { finished = resolve; });
        await query().click();
        await started;
        await page.getByRole('button', { name: '任务 B' }).click();
        release();
        release = undefined;
        await settled;
        await page.waitForTimeout(100);
        await panel().getByText('尚未查询', { exact: false }).waitFor();
        assert.ok(!(await panel().innerText()).includes('身份与记录一致'));
        mode = 'directory_missing';
        await query().click();
        await panel().getByText('目录不存在', { exact: false }).waitFor();
        checks.push('task switch excludes old snapshot');

        mode = 'delayed';
        started = new Promise(resolve => { entered = resolve; });
        settled = new Promise(resolve => { finished = resolve; });
        await query().click();
        await started;
        await page.getByRole('button', { name: '关闭详情' }).click();
        release();
        release = undefined;
        await settled;
        await page.waitForTimeout(100);
        await page.getByRole('button', { name: '打开详情' }).click();
        await panel().getByText('尚未查询', { exact: false }).waitFor();
        checks.push('unmount excludes delayed response');

        const beforeInvalid = requests;
        await page.getByRole('button', { name: '无效任务' }).click();
        assert.ok(await query().isDisabled());
        assert.equal(requests, beforeInvalid);
        await page.getByRole('button', { name: '任务 A' }).click();

        mode = 'identity_matches_record';
        await query().click();
        await panel().getByText('身份与记录一致', { exact: false }).waitFor();
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await panel().scrollIntoViewIfNeeded();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.screenshot({ path: path.join(output, `sample-cleanup-preflight-${width}.png`), fullPage: true });
        }
        assert.equal(writes, 0);
        assert.deepEqual(errors, []);
        await writeFile(path.join(output, 'evidence.json'), JSON.stringify({ checks, requests, writes, widths: [1366, 1920], pageErrors: errors }, null, 2));
        console.log(`PASS sample cleanup preflight component: ${checks.join(', ')}`);
    } finally {
        await browser.close();
    }
}
