import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
        page.setDefaultTimeout(8000);
        const errors = [], requests = [], held = [];
        page.on('pageerror', error => errors.push(error.message));
        // 故意让assessment传输忽略abort，用迟到成功回执验证组件身份隔离。
        // 超时仍使用原生AbortSignal，只在测试指定时缩短预算。
        await page.addInitScript(() => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => String(input).endsWith('/assessment') && window.ignoreAssessmentAbort
                ? originalFetch(input, { ...init, signal: undefined }) : originalFetch(input, init);
            const timeout = AbortSignal.timeout.bind(AbortSignal);
            AbortSignal.timeout = milliseconds => timeout(window.shortAssessmentTimeout ? 30 : milliseconds);
        });
        let mode = 'ok', result = 'exclusive_access_unconfirmed';
        let grant = { grant_id: 'e'.repeat(32), revision: 1, status: 'enabled' };
        await page.route('**/api/**', async route => {
            const request = route.request(), url = new URL(request.url());
            const proposal = url.pathname.split('/file-edit-proposals/')[1].split('/')[0];
            const ids = { workspace_id: 'a'.repeat(32), task_id: 'b'.repeat(32), proposal_id: proposal };
            requests.push({ method: request.method(), path: url.pathname, body: request.postData() });
            if (url.pathname.endsWith('/assessment')) {
                assert.equal(request.method(), 'POST');
                assert.deepEqual(request.postDataJSON(), { grant_id: grant.grant_id, revision: grant.revision, apply_requested: true });
                if (mode === 'hold') { held.push(() => route.fulfill({ json: { ...ids, result: 'grant_revoked' } }).catch(() => {})); return; }
                if (mode === 'network') { await route.abort(); return; }
                if (mode === 'error') { await route.fulfill({ status: 500, json: { code: 'PRIVATE', message: 'PRIVATE' } }); return; }
                if (mode === 'malformed') { await route.fulfill({ status: 200, body: 'PRIVATE' }); return; }
                await route.fulfill({ json: { ...ids, result, ...(mode === 'mismatch' ? { task_id: 'f'.repeat(32) } : {}) } });
                return;
            }
            if (url.pathname.endsWith('/write-grant') && mode === 'grant-error') { await route.fulfill({ status: 500, body: 'PRIVATE' }); return; }
            if (url.pathname.endsWith('/write-grant')) { await route.fulfill({ json: { ...ids, grant } }); return; }
            // 本专项不允许任何变更请求。
            assert.equal(request.method(), 'GET');
            await route.fulfill({ json: { ...ids, status: 'approved', relative_path: 'file.txt', baseline_sha256: 'e'.repeat(64), proposed_sha256: 'f'.repeat(64), diff: '-old\n+new', diff_truncated: false, created_at: '2026-09-29T00:00:00Z' } });
        });
        const button = () => page.getByRole('button', { name: '检查写入条件', exact: true });
        const section = () => page.getByRole('region', { name: '写入条件检查' });
        const count = () => requests.filter(r => r.path.endsWith('/assessment')).length;
        async function open() {
            await page.getByRole('button', { name: '查看提案详情', exact: true }).click();
            await page.locator('summary').filter({ hasText: '普通项目写入许可' }).click();
        }
        async function query() {
            await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
            await button().waitFor();
        }
        async function waitCount(n) {
            await page.waitForFunction(() => document.querySelector('button') !== null);
            for (let i = 0; count() < n && i < 80; i++) await page.waitForTimeout(20);
            assert.equal(count(), n);
        }
        await page.goto(base); await open();
        assert.equal(count(), 0);
        assert.equal(await button().count(), 0);
        await query();
        assert.equal(count(), 0);
        await button().focus(); await page.keyboard.press('Enter');
        await page.getByText('检查时未满足写入条件：无法确认文件的排他访问条件。', { exact: true }).waitFor();
        assert.equal(count(), 1);
        // 截图使用真实详情组件和生产样式，检查桌面宽度与键盘焦点。
        await mkdir('output/playwright/project-write-assessment', { recursive: true });
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await button().focus();
            assert.ok(await button().evaluate(el => el === document.activeElement));
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `output/playwright/project-write-assessment/${width}.png`, fullPage: true });
        }
        for (const failure of ['error', 'network', 'malformed', 'mismatch', 'eligible', 'unknown']) {
            mode = ['eligible', 'unknown'].includes(failure) ? 'ok' : failure;
            result = failure === 'eligible' ? 'eligible' : failure === 'unknown' ? 'PRIVATE' : 'exclusive_access_unconfirmed';
            const before = count(); await button().click();
            await section().getByRole('alert').waitFor();
            assert.ok((await section().innerText()).includes('当前结果未知'));
            assert.ok(!(await section().innerText()).includes('PRIVATE'));
            assert.equal(await section().getByRole('status').count(), 0);
            await page.waitForTimeout(60); assert.equal(count(), before + 1);
        }
        // 正常fetch收到超时信号便结束，无需等待上游回执。
        mode = 'hold'; await page.evaluate(() => { window.shortAssessmentTimeout = true; });
        let before = count(); await button().click(); await waitCount(before + 1);
        await section().getByRole('alert').waitFor(); await held.shift()();
        await page.evaluate(() => { window.shortAssessmentTimeout = false; window.ignoreAssessmentAbort = true; });
        mode = 'hold';
        before = count();
        await button().evaluate(el => { el.click(); el.click(); });
        await waitCount(before + 1);
        await page.getByRole('button', { name: '取消检查', exact: true }).click();
        await section().getByText('检查已取消，当前结果未知。', { exact: true }).waitFor();
        // 新检查先完成，旧请求再返回，不能覆盖新结果。
        mode = 'ok'; result = 'exclusive_access_unconfirmed'; await button().click();
        await section().getByRole('status').waitFor(); await held.shift()();
        await page.waitForTimeout(80);
        assert.ok((await section().innerText()).includes('无法确认文件的排他访问条件'));
        assert.ok(!(await section().innerText()).includes('许可已撤销'));
        // 折叠关闭会卸载检查实例；重新展开无自动请求、无旧结果。
        mode = 'hold'; before = count(); await button().click(); await waitCount(before + 1);
        await page.locator('summary').filter({ hasText: '普通项目写入许可' }).click();
        await section().waitFor({ state: 'detached' }); await held.shift()();
        await page.locator('summary').filter({ hasText: '普通项目写入许可' }).click();
        await button().waitFor(); assert.equal(await section().getByRole('status').count(), 0);
        assert.equal(count(), before + 1);
        // 完整关闭详情也销毁请求实例，重开不会恢复旧诊断。
        mode = 'hold'; before = count(); await button().click(); await waitCount(before + 1);
        await page.getByRole('button', { name: '收起详情', exact: true }).click();
        await section().waitFor({ state: 'detached' }); await held.shift()();
        mode = 'ok'; await open(); await query();
        assert.equal(await section().getByRole('status').count(), 0);
        assert.equal(count(), before + 1);
        // 重新读取许可修订清除旧诊断上下文，撤销记录也仅允许诊断。
        mode = 'hold'; before = count(); await button().click(); await waitCount(before + 1);
        grant = { ...grant, revision: 2, status: 'revoked' }; await query(); await held.shift()();
        assert.equal(await section().getByRole('status').count(), 0);
        mode = 'ok'; result = 'grant_revoked'; await button().click();
        await section().getByText('检查时未满足写入条件：许可已撤销。', { exact: true }).waitFor();
        // 超时后的迟到正文不能变成成功。
        mode = 'hold'; await page.evaluate(() => { window.shortAssessmentTimeout = true; });
        before = count(); await button().click(); await waitCount(before + 1);
        await page.waitForTimeout(70); await held.shift()();
        await section().getByRole('alert').waitFor();
        await page.evaluate(() => { window.shortAssessmentTimeout = false; });
        // 切换提案时旧请求即便成功返回，也不能污染新资源。
        mode = 'hold'; before = count(); await button().click(); await waitCount(before + 1);
        await page.getByRole('button', { name: '切换提案' }).click(); await held.shift()();
        mode = 'ok'; await open(); await query();
        assert.equal(await section().getByRole('status').count(), 0);
        assert.equal(count(), before + 1);
        await page.reload(); await open(); await query();
        assert.equal(count(), before + 1);
        assert.equal(await section().getByRole('status').count(), 0);
        // 许可读取失败和未确认的撤销均不能由诊断入口绕开门禁。
        mode = 'grant-error'; await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
        await page.getByText('许可查询失败，请重新查询。', { exact: true }).waitFor();
        assert.equal(await button().count(), 0);
        mode = 'ok'; grant = { ...grant, revision: 1, status: 'enabled' };
        await page.evaluate(() => sessionStorage.setItem(`project-write-grant:${'a'.repeat(32)}:${'b'.repeat(32)}:${'d'.repeat(32)}`, `revoke:${'e'.repeat(32)}`));
        await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
        await page.getByText('先前变更结果仍未确认，仅可继续查询，不能再次提交。', { exact: true }).waitFor();
        assert.equal(await button().count(), 0);
        // 无许可也不暴露检查入口。
        await page.evaluate(() => sessionStorage.clear());
        grant = null; await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
        await page.getByText('查询时许可：未发放', { exact: true }).waitFor();
        assert.equal(await button().count(), 0);
        assert.ok(requests.every(r => r.method === 'GET' || r.path.endsWith('/assessment')));
        assert.deepEqual(errors, []);
        await writeFile('output/playwright/project-write-assessment/evidence.json', JSON.stringify({
            result: 'passed', viewports: [1366, 1920], assessmentRequests: count(), mutationRequests: 0,
            boundary: 'real components; mocked BFF responses; no database or backend integration',
        }, null, 4));
        console.log('PASS assessment PC: explicit/keyboard, unknown, double click, cancel/stale, fold, revision, timeout, resource switch, reload, no mutations; 1366/1920');
    } finally { await browser.close(); }
}
