import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../output/playwright/proposal-status-review');
    await mkdir(output, { recursive: true });
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });

    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
        const errors = [];
        const counts = { detail: 0, sample: 0, application: 0, writes: 0 };
        let sampleMode = 'ready';
        let applicationMode = 'idle';
        page.on('pageerror', error => errors.push(error.message));
        page.on('request', request => {
            if (request.url().includes('/api/') && request.method() !== 'GET') counts.writes++;
        });

        await page.route('**/api/**', async route => {
            const request = route.request();
            assert.equal(request.method(), 'GET');
            const url = new URL(request.url());
            assert.equal(url.search, '');
            const ids = {
                workspace_id: 'a'.repeat(32),
                task_id: 'b'.repeat(32),
            };
            if (url.pathname.endsWith('/sample-status')) {
                counts.sample++;
                await route.fulfill(sampleMode === 'error'
                    ? { status: 500, body: 'PRIVATE sample failure' }
                    : { json: { ...ids, status: sampleMode } });
                return;
            }
            if (url.pathname.endsWith('/application-status')) {
                counts.application++;
                await route.fulfill(applicationMode === 'error'
                    ? { status: 500, body: 'PRIVATE application failure' }
                    : { json: { ...ids, proposal_id: 'c'.repeat(32), application_status: applicationMode } });
                return;
            }
            if (url.pathname.endsWith(`/file-edit-proposals/${'c'.repeat(32)}`)) {
                counts.detail++;
                await route.fulfill({ json: {
                    ...ids, proposal_id: 'c'.repeat(32), status: 'approved', relative_path: 'file.txt',
                    baseline_sha256: 'd'.repeat(64), proposed_sha256: 'e'.repeat(64),
                    diff: '-old\n+new', diff_truncated: false, created_at: '2026-09-23T00:00:00Z',
                } });
                return;
            }
            throw new Error(`Unexpected API request: ${url.pathname}`);
        });

        await page.goto(base);
        const detail = page.getByRole('region', { name: '文件修改提案详情', exact: true });
        await detail.getByRole('button', { name: '查看提案详情' }).waitFor();
        assert.deepEqual(counts, { detail: 0, sample: 0, application: 0, writes: 0 });
        await detail.getByRole('button', { name: '查看提案详情' }).click();
        const review = detail.getByRole('region', { name: '样例与提案状态核对', exact: true });
        const sample = review.getByRole('region', { name: '受限样例登记状态', exact: true });
        const application = review.getByRole('region', { name: '提案应用状态', exact: true });
        await sample.getByRole('button', { name: '查询登记状态' }).waitFor();
        assert.deepEqual(counts, { detail: 1, sample: 0, application: 0, writes: 0 });

        await sample.getByRole('button', { name: '查询登记状态' }).click();
        await sample.getByText('查询时登记可供后续门禁检查').waitFor();
        assert.equal(counts.sample, 1);
        assert.equal(counts.application, 0);
        await application.getByRole('button', { name: '查询应用状态' }).click();
        await application.getByText('查询时应用状态：尚未领取执行').waitFor();
        assert.equal(counts.application, 1);
        assert.ok((await review.innerText()).includes('两份结果来自独立查询'));
        assert.ok((await review.innerText()).includes('不能据此执行提案'));

        sampleMode = 'error';
        await sample.getByRole('button', { name: '重新查询登记状态' }).click();
        await sample.getByRole('alert').waitFor();
        assert.ok((await application.innerText()).includes('尚未领取执行'));
        assert.ok(!(await review.innerText()).includes('PRIVATE'));

        sampleMode = 'sealed';
        await sample.getByRole('button', { name: '重新查询登记状态' }).click();
        await sample.getByText('查询时登记已封锁或绑定不匹配').waitFor();
        applicationMode = 'error';
        await application.getByRole('button', { name: '重新查询应用状态' }).click();
        await application.getByRole('alert').waitFor();
        assert.ok((await sample.innerText()).includes('登记已封锁'));
        assert.ok(!(await review.innerText()).includes('PRIVATE'));

        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await review.scrollIntoViewIfNeeded();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.screenshot({ path: path.join(output, `review-${width}.png`), fullPage: true });
        }
        assert.equal(counts.writes, 0);
        assert.deepEqual(errors, []);
        await writeFile(path.join(output, 'evidence.json'), JSON.stringify({ counts, widths: [1366, 1920], pageErrors: errors }, null, 2));
        console.log('PASS proposal status review: independent GETs, partial failure, no writes, two PC widths');
    } finally {
        await browser.close();
    }
}
