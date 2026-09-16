import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const markdown = '# 能力介绍\n\n你好！**查询时间**和*计算面积*。\n\n1. 查询时间\n2. 计算面积\n\n- [x] 支持 Markdown\n\n> 引用说明\n\n行内 `const value = 1`\n\n```typescript\nconst value = "' + 'long'.repeat(90) + '";\n```\n\n| 功能 | 结果 |\n| --- | --- |\n| 时间 | 12:00 |\n\n[正常链接](https://example.com)\n\n[危险链接](javascript:alert(1))\n\n<script>window.injected=true</script>\n\n![示例图片](https://example.com/image.png)\n';
try {
    // 将模拟 NDJSON 按时间分块交付给真实 reader，观察未闭合 Markdown 的中间态。
    await page.addInitScript(() => {
        const original = window.fetch.bind(window);
        window.fetch = async (input, init) => {
            const response = await original(input, init);
            if (!String(input).includes('/api/chat/stream')) return response;
            const lines = (await response.text()).split('\n').filter(Boolean);
            let timer;
            const stream = new ReadableStream({
                start(controller) {
                    let index = 0;
                    const next = () => {
                        if (index === lines.length) { controller.close(); return; }
                        controller.enqueue(new TextEncoder().encode(lines[index++] + '\n'));
                        timer = setTimeout(next, 150);
                    };
                    next();
                },
                cancel() { clearTimeout(timer); },
            });
            return new Response(stream, { status: response.status, headers: response.headers });
        };
    });
    const projectResponse = await page.request.post(`${base}/api/workspaces`, { headers: { Origin: base }, data: { name: 'Markdown 验收' } });
    assert.equal(projectResponse.status(), 201);
    const workspace = await projectResponse.json();
    const taskResponse = await page.request.post(`${base}/api/workspaces/${workspace.external_id}/tasks`, { headers: { Origin: base }, data: { title: 'Markdown 回复' } });
    assert.equal(taskResponse.status(), 201);
    const task = await taskResponse.json();
    await page.route('**/api/chat/stream', route => {
        const lines = [{ type: 'TEXT_MESSAGE_START' }];
        for (let i = 0; i < markdown.length; i += 50) lines.push({ type: 'TEXT_MESSAGE_CONTENT', chunk: markdown.slice(i, i + 50) });
        lines.push({ type: 'TEXT_MESSAGE_END' });
        lines.push({ type: 'RUN_ERROR', code: 'test_end', message: '测试结束' });
        return route.fulfill({ contentType: 'application/x-ndjson', body: lines.map(line => JSON.stringify(line)).join('\n') + '\n' });
    });
    await page.goto(`${base}/?workspace=${workspace.external_id}&task=${task.external_id}`);
    await page.getByLabel('你的问题').fill('**用户原文**');
    await page.getByLabel('你的问题').press('Enter');
    const answer = page.getByRole('region', { name: 'AI 回复', exact: true });
    await answer.locator('strong').getByText('查询时间', { exact: true }).waitFor();
    assert.equal(await answer.locator('table').count(), 0);
    console.log('PASS incremental Markdown renders before stream completes');
    await answer.getByRole('link', { name: '示例图片' }).waitFor();
    assert.equal(await answer.locator('h1').innerText(), '能力介绍');
    assert.equal(await answer.locator('ol > li').count(), 2);
    assert.equal(await answer.locator('input[type=checkbox]').isChecked(), true);
    assert.equal(await answer.locator('table tbody tr').count(), 1);
    assert.equal(await answer.locator('script, img').count(), 0);
    assert.equal(await answer.locator('a[href^="javascript:"]').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await answer.getByRole('link', { name: '正常链接' }).getAttribute('rel'), 'noopener noreferrer');
    assert.ok(await answer.locator('pre').evaluate(node => node.scrollWidth > node.clientWidth));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log('PASS formatting, safe HTML/links and contained code scrolling');

    await page.route('**/api/workspaces/*/tasks/*/messages', route => route.fulfill({ json: { messages: [
        { role: 'user', content: '**用户原文**' }, { role: 'assistant', content: markdown },
    ] } }));
    await page.reload();
    await page.locator('strong').getByText('查询时间', { exact: true }).waitFor();
    assert.equal(await page.getByText('**用户原文**', { exact: true }).count(), 1);
    assert.equal(await page.locator('table').count(), 1);
    await mkdir('/private/tmp/agent-ui-preview/output/playwright', { recursive: true });
    for (const scheme of ['light', 'dark']) {
        await page.emulateMedia({ colorScheme: scheme });
        await page.screenshot({ path: `/private/tmp/agent-ui-preview/output/playwright/markdown-${scheme}.png`, fullPage: true });
    }
    console.log('PASS restored assistant Markdown, literal user text and light/dark layouts');
    assert.deepEqual(errors, []);
} finally {
    await context.close();
    await browser.close();
}
