import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        let grant = null, posts = 0, mode = 'ok', held;
        await page.route('**/api/**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            const proposal = url.pathname.split('/file-edit-proposals/')[1].split('/')[0];
            const ids = { workspace_id: 'a'.repeat(32), task_id: 'b'.repeat(32), proposal_id: proposal };
            if (!url.pathname.includes('/write-grant')) {
                await route.fulfill({ json: { ...ids, status: 'approved', relative_path: 'file.txt', baseline_sha256: 'e'.repeat(64), proposed_sha256: 'f'.repeat(64), diff: '-old\n+new', diff_truncated: false, created_at: '2026-09-28T00:00:00Z' } }); return;
            }
            if (request.method() === 'POST') {
                posts++;
                if (mode === 'unknown') { await route.abort(); return; }
                if (url.pathname.endsWith('/revoke')) {
                    assert.deepEqual(request.postDataJSON(), { grant_id: 'e'.repeat(32), revision: 1 });
                    grant = { ...grant, revision: 2, status: 'revoked' };
                } else {
                    assert.deepEqual(request.postDataJSON(), { action: 'grant' });
                    grant = { grant_id: 'e'.repeat(32), revision: 1, status: 'enabled' };
                }
                await new Promise(resolve => setTimeout(resolve, 80));
                await route.fulfill({ status: url.pathname.endsWith('/revoke') ? 200 : 201, json: { ...ids, grant } }); return;
            }
            if (mode === 'hold') { held = () => route.fulfill({ json: { ...ids, grant } }).catch(() => {}); return; }
            if (mode === 'error') { await route.fulfill({ status: 500, body: 'PRIVATE' }); return; }
            await route.fulfill({ json: { ...ids, grant } });
        });
        async function open() {
            await page.getByRole('button', { name: '查看提案详情', exact: true }).click();
            const panel = page.getByRole('group', { name: '普通项目写入许可' });
            await page.locator('summary').filter({ hasText: '普通项目写入许可' }).click();
            return panel;
        }
        await page.goto(base);
        await open();
        assert.equal(posts, 0);
        const query = () => page.getByRole('button', { name: '查询许可状态' }).click();
        await query(); await page.getByText('查询时许可：未发放', { exact: true }).waitFor();
        await page.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        assert.equal(posts, 0);
        await page.getByRole('button', { name: '取消', exact: true }).click();
        assert.equal(posts, 0);
        await page.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        await page.getByRole('button', { name: '确认发放许可' }).evaluate(button => { button.click(); button.click(); });
        await page.getByText('查询时许可：已启用（不代表当前可写）', { exact: true }).waitFor();
        assert.equal(posts, 1);
        await page.getByRole('button', { name: '撤销本提案许可', exact: true }).click();
        await page.getByRole('button', { name: '确认撤销许可' }).click();
        await page.getByText('查询时许可：已撤销，不可重新启用', { exact: true }).waitFor();
        assert.equal(posts, 2);
        assert.equal(await page.getByRole('button', { name: '发放本提案许可', exact: true }).count(), 0);
        mode = 'error'; await query(); await page.getByText('许可查询失败，请重新查询。', { exact: true }).waitFor();
        assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));
        mode = 'ok'; grant = null;
        await page.getByRole('button', { name: '切换提案' }).click(); await open(); await query();
        await page.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        mode = 'unknown'; await page.getByRole('button', { name: '确认发放许可' }).click();
        await page.getByText('许可变更结果未确认，请先查询状态，勿重复提交。', { exact: true }).waitFor();
        assert.equal(posts, 3);
        mode = 'ok'; await query(); await page.getByText('先前变更结果仍未确认，仅可继续查询，不能再次提交。', { exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: '发放本提案许可', exact: true }).count(), 0);
        await page.reload(); await open(); await query();
        await page.getByText('先前变更结果仍未确认，仅可继续查询，不能再次提交。', { exact: true }).waitFor();
        mode = 'hold'; await query();
        await page.getByRole('button', { name: '切换提案' }).click(); mode = 'ok'; await open();
        if (held) await held();
        assert.equal(await page.getByText('查询时许可：未发放', { exact: true }).count(), 0);
        await query(); await page.getByText('查询时许可：未发放', { exact: true }).waitFor();
        assert.equal(posts, 3);
        await mkdir('output/playwright/project-write-grant', { recursive: true });
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
            await page.screenshot({ path: `output/playwright/project-write-grant/${width}.png`, fullPage: true });
        }
        assert.deepEqual(errors, []);
        console.log('PASS grant UI: explicit confirmation, double click, revoke, unknown/reload guard, stale response, PC 1366/1920');
    } finally { await browser.close(); }
}
