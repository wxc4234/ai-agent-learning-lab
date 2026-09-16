import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Use isolated launcher');
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_EXECUTABLE});
let count = 0;
async function scenario(name, run) {
    const context = await browser.newContext({viewport:{width:1440,height:900}});
    try {
        const page = await context.newPage();
        page.setDefaultTimeout(20000);
        await run(page,context);
        count++;
        console.log(`PASS: ${name}`);
    } finally { await context.close(); }
}
try {
    await scenario('local: no login, real chat and cancellation', async (page,context) => {
        // 本地聊天从项目草稿首发创建 Task，不再隐式创建无任务会话。
        const project = await context.request.post(`${base}/api/workspaces`, {
            headers: { Origin: base }, data: { name: '本机聊天项目' },
        });
        assert.equal(project.status(), 201);
        await page.goto(base);
        await page.getByRole('button', { name: '在 本机聊天项目 新建任务', exact: true }).click();
        await page.getByLabel('你的问题').waitFor();
        assert.equal((await context.cookies()).some(cookie => cookie.name === 'agent_session'),false);
        await page.getByLabel('你的问题').fill('本机聊天测试');
        await page.getByRole('button',{name:'发送',exact:true}).click();
        await page.getByText('隔离模型：认证聊天成功。',{exact:true}).waitFor();
        // 运行详情默认收起，展开后观察工具事件与取消终态。
        await page.getByRole('button', { name: '展开详情', exact: true }).click();
        await page.getByLabel('你的问题').fill('[cancel-test] 本机取消');
        await page.getByRole('button',{name:'发送',exact:true}).click();
        await page.getByRole('button',{name:'停止生成',exact:true}).waitFor();
        // 等运行创建完成再点击，验证真实取消 BFF 而非仅取消尚未发送的请求。
        await page.getByText('calculate_rectangle_area',{exact:false}).first().waitFor();
        const cancelled = page.waitForResponse(response => response.url().includes('/cancel') && response.request().method() === 'POST');
        await page.getByRole('button',{name:'停止生成',exact:true}).click();
        assert.equal((await cancelled).status(),204);
        await page.getByText('已停止生成',{exact:true}).waitFor();
        await page.goto(`${base}/login`);
        await page.getByLabel('你的问题').waitFor();
        assert.equal(new URL(page.url()).pathname,'/');
    });
    await scenario('local: PC creation, validation, reload and safe response', async (page,context) => {
        await page.goto(`${base}/workspaces/new`);
        await page.getByLabel('工作空间名称',{exact:true}).fill('   ');
        await page.getByRole('button',{name:'创建工作空间',exact:true}).click();
        await page.getByRole('main').getByRole('alert').waitFor();
        await page.getByLabel('工作空间名称',{exact:true}).fill('  本机项目  ');
        const created = page.waitForResponse(response => response.url().endsWith('/api/workspaces'));
        await page.getByRole('button',{name:'创建工作空间',exact:true}).click();
        const response = await created;
        assert.equal(response.status(),201);
        assert.deepEqual(Object.keys(await response.json()).sort(),['created_at','external_id','name']);
        await page.getByText('工作空间已创建',{exact:true}).waitFor();
        assert.equal(await page.getByRole('button',{name:'创建工作空间',exact:true}).isDisabled(),true);
        await mkdir('/private/tmp/agent-ui-preview',{recursive:true});
        for (const [width,height] of [[1366,768],[1920,1080]]) {
            await page.setViewportSize({width,height});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
            await page.screenshot({path:`/private/tmp/agent-ui-preview/local-workspace-${width}.png`,fullPage:true});
        }
        const before = await (await context.request.get(`${base}/api/auth/me`)).json();
        await page.reload();
        await page.getByLabel('工作空间名称',{exact:true}).waitFor();
        const after = await (await context.request.get(`${base}/api/auth/me`)).json();
        assert.deepEqual(before,after);
        assert.equal(before.external_id,'local-owner-v1');
        assert.equal((await context.cookies()).some(cookie => cookie.name === 'agent_session'),false);
    });
    await scenario('local: uncertain creation does not retry', async (page) => {
        let calls=0;
        await page.route('**/api/workspaces',route => {calls++;return route.fulfill({status:504,body:'{}'});});
        await page.goto(`${base}/workspaces/new`);
        await page.getByLabel('工作空间名称',{exact:true}).fill('结果未确认');
        await page.getByRole('button',{name:'创建工作空间',exact:true}).click();
        await page.getByRole('main').getByRole('alert').waitFor();
        assert.equal(await page.getByRole('button',{name:'创建工作空间',exact:true}).isDisabled(),true);
        assert.equal(calls,1);
    });
    console.log(`Local PC scenarios: ${count} passed`);
} finally { await browser.close(); }
