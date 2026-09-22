import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../output/playwright/execution-component');
    await mkdir(output, { recursive: true });
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    const checks = [];
    try {
        const page = await browser.newPage({ viewport: { width: 1366, height: 900 }, colorScheme: 'light' });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let requests = 0, mode = 'success', release;
        const receipt = url => ({
            workspace_id: 'a'.repeat(32), task_id: 'b'.repeat(32),
            proposal_id: new URL(url).pathname.split('/').at(-2),
            file_status: 'replaced', application_status: mode === 'unknown' ? 'unknown' : 'applied',
            code: mode === 'unknown' ? 'proposal_application_registration_unconfirmed' : 'proposal_application_applied',
            cleanup_complete: true,
        });
        await page.route('**/api/workspaces/**/apply', async route => {
            requests++;
            assert.equal(route.request().method(), 'POST');
            assert.deepEqual(route.request().postDataJSON(), { action: 'apply' });
            const body = mode === 'invalid' ? { private: 'PRIVATE' } : receipt(route.request().url());
            if (mode === 'pending') await new Promise(resolve => { release = resolve; });
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
        });
        const apply = () => page.getByRole('button', { name: '应用样例提案', exact: true });
        const checkbox = () => page.getByRole('checkbox');
        async function fresh(next = 'success') {
            if (release) { release(); release = undefined; }
            mode = next;
            await page.goto(base);
            await page.evaluate(() => sessionStorage.clear());
            await page.reload();
            await checkbox().waitFor();
            assert.ok(await apply().isDisabled());
        }
        async function submit() { await checkbox().check(); await apply().click(); }
        async function phase(text) { await page.getByText(text, { exact: false }).first().waitFor(); }
        await fresh();
        assert.equal(await page.locator('html').getAttribute('data-mounts'), '2');
        assert.equal(requests, 0);
        const layout = await page.locator('section').first().evaluate(element => ({
            width: element.clientWidth, scroll: element.scrollWidth,
            children: [...element.querySelectorAll('p')].map(p => ({ width: p.clientWidth, scroll: p.scrollWidth, whiteSpace: getComputedStyle(p).whiteSpace, wordBreak: getComputedStyle(p).wordBreak })),
        }));
        assert.equal(layout.scroll, layout.width);
        for (const child of layout.children) assert.equal(child.scroll, child.width);
        await page.screenshot({ path: path.join(output, 'laptop-idle.png'), fullPage: true });
        // 真实键盘切换复选框、聚焦按钮并提交；StrictMode不得重复发请求。
        await checkbox().focus(); await page.keyboard.press('Space');
        await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
        await phase('已确认登记应用成功');
        assert.equal(requests, 1);
        assert.ok(await apply().isDisabled());
        await page.reload(); await phase('已确认登记应用成功');
        assert.equal(requests, 1);
        checks.push('StrictMode/keyboard/single-request/refresh-receipt');
        await page.setViewportSize({ width: 1920, height: 1080 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: path.join(output, 'desktop-success.png'), fullPage: true });
        await fresh('pending'); const beforeCancel = requests;
        await submit(); await page.getByRole('button', { name: '停止等待' }).click();
        await phase('已阻止再次提交');
        release(); release = undefined;
        await page.reload(); await phase('已阻止再次提交');
        assert.equal(requests, beforeCancel + 1);
        checks.push('stop-waiting/late-response/persistent-guard');
        await fresh('pending'); await submit(); await page.getByRole('button', { name: '停止等待' }).waitFor();
        await page.getByRole('button', { name: '样例 B', exact: true }).click();
        await checkbox().waitFor(); assert.equal(await checkbox().isChecked(), false);
        mode = 'success'; await submit(); await phase('已确认登记应用成功');
        release(); release = undefined;
        await page.getByRole('button', { name: '样例 A', exact: true }).click();
        await phase('已阻止再次提交');
        checks.push('switch-scope/confirmation-reset/late-response-isolation');
        await fresh('pending'); await submit(); await page.getByRole('button', { name: '停止等待' }).waitFor();
        await page.getByRole('button', { name: '卸载组件', exact: true }).click();
        release(); release = undefined;
        await page.getByRole('button', { name: '挂载组件', exact: true }).click();
        await phase('已阻止再次提交');
        checks.push('unmount/remount/persistent-guard');
        await fresh('unknown'); await submit(); await phase('未确认数据库登记结果');
        assert.ok(await apply().isDisabled());
        await page.reload(); await phase('未确认数据库登记结果');
        await page.setViewportSize({ width: 1366, height: 900 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: path.join(output, 'laptop-unknown.png'), fullPage: true });
        checks.push('unknown-receipt/refresh/no-overflow');
        await fresh('invalid'); await submit(); await phase('已阻止再次提交');
        assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE'));
        checks.push('invalid-receipt/safe-message');
        // 页面加载前故障注入真实Storage API，不模拟React Hook。
        await page.addInitScript(() => Object.defineProperty(window, 'sessionStorage', { get() { throw Error('PRIVATE'); } }));
        await page.reload(); await phase('无法安全读取或保存提交记录');
        assert.ok(await apply().isDisabled());
        checks.push('storage-unavailable');
        assert.deepEqual(errors, []);
        await writeFile(path.join(output, 'evidence.json'), JSON.stringify({ checks, requests, layout, pageErrors: errors, viewports: ['1366x900', '1920x1080'], backend: 'intercepted; no filesystem writes' }, null, 4));
        console.log(JSON.stringify({ checks: checks.length, requests, output }));
    } finally { await browser.close(); }
}
