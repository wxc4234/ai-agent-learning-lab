import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
const mode = process.env.BROWSER_APP_MODE;
if (!base || !['account','local'].includes(mode)) throw new Error('Use isolated launcher');
const browser = await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE});
let count=0;
async function login(page) {
    if (mode === 'local') return;
    await page.goto(`${base}/login`);
    await page.getByLabel('用户名',{exact:true}).fill('浏览器Agent');
    await page.getByLabel('密码',{exact:true}).fill('Isolated-Browser-Test-2026!');
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await page.getByRole('button',{name:'退出登录',exact:true}).waitFor();
}
async function scenario(name, run) {
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    try {
        const page=await context.newPage();
        page.setDefaultTimeout(20000);
        await login(page);
        await run(page,context);
        count++;
        console.log(`PASS ${mode}: ${name}`);
    } finally { await context.close(); }
}
try {
    await scenario('empty, real create, return and PC reload', async (page,context) => {
        await page.goto(`${base}/workspaces`);
        await page.getByText('还没有工作空间',{exact:true}).waitFor();
        await page.getByRole('link',{name:'创建工作空间',exact:true}).click();
        await page.getByLabel('工作空间名称',{exact:true}).fill('  列表流程项目  ');
        await page.getByRole('button',{name:'创建工作空间',exact:true}).click();
        await page.getByText('工作空间已创建',{exact:true}).waitFor();
        await page.getByRole('link',{name:'返回工作空间列表',exact:true}).click();
        await page.getByRole('cell',{name:'列表流程项目',exact:true}).waitFor();
        await page.reload();
        await page.getByRole('cell',{name:'列表流程项目',exact:true}).waitFor();
        await mkdir('/private/tmp/agent-ui-preview',{recursive:true});
        for (const [width,height] of [[1366,768],[1920,1080]]) {
            await page.setViewportSize({width,height});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
            await page.screenshot({path:`/private/tmp/agent-ui-preview/workspace-list-${mode}-${width}.png`,fullPage:true});
        }
        if (mode === 'local') assert.equal((await context.cookies()).some(cookie=>cookie.name==='agent_session'),false);
    });
    await scenario('failure is not empty, retry and malformed response', async (page) => {
        let status=503;
        let body='{}';
        await page.route('**/api/workspaces?*',route=>route.fulfill({status,contentType:'application/json',body}));
        await page.goto(`${base}/workspaces`);
        await page.getByRole('main').getByRole('alert').waitFor();
        assert.equal(await page.getByText('还没有工作空间',{exact:true}).count(),0);
        status=200;
        body=JSON.stringify({items:[],has_more:false});
        await page.getByRole('button',{name:'重新加载',exact:true}).click();
        await page.getByText('还没有工作空间',{exact:true}).waitFor();
        body=JSON.stringify({items:[],has_more:true});
        await page.getByRole('button',{name:'刷新列表',exact:true}).click();
        await page.getByRole('main').getByRole('alert').waitFor();
        assert.equal(await page.getByText('还没有工作空间',{exact:true}).count(),0);
    });
    await scenario('inflight refresh blocked and navigation cancels stale view', async (page) => {
        let calls=0;
        let release;
        const started=new Promise(resolve=>{
            page.route('**/api/workspaces?*',async route=>{
                calls++;
                resolve();
                await new Promise(done=>{release=done;});
                await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[],has_more:false})}).catch(()=>{});
            });
        });
        await page.goto(`${base}/workspaces`);
        await started;
        const loading=page.getByRole('button',{name:'正在加载…',exact:true});
        assert.equal(await loading.isDisabled(),true);
        await loading.dispatchEvent('click');
        await loading.dispatchEvent('click');
        assert.equal(calls,1);
        await page.getByRole('link',{name:'聊天首页',exact:true}).click();
        await page.getByLabel('你的问题').waitFor();
        release();
        await page.unroute('**/api/workspaces?*');
        await page.getByRole('link',{name:'工作空间',exact:true}).click();
        await page.getByRole('cell',{name:'列表流程项目',exact:true}).waitFor();
    });
    await scenario('real truncation at 20 and preserved creation behavior', async (page,context) => {
        for(let i=0;i<20;i++) {
            const response=await context.request.post(`${base}/api/workspaces`,{
                headers:{Origin:base},data:{name:`列表数量测试 ${i}`},
            });
            assert.equal(response.status(),201);
        }
        await page.goto(`${base}/workspaces`);
        await page.getByText('当前仅显示最近 20 个工作空间，',{exact:false}).waitFor();
        assert.equal(await page.locator('tbody tr').count(),20);
        const response=await context.request.get(`${base}/api/workspaces?limit=100`);
        assert.equal(response.status(),200);
        const data=await response.json();
        assert.equal(data.items.length,21);
        assert.equal(data.has_more,false);
    });
    if(mode==='account') {
        await scenario('401 returns to the list after login', async (page) => {
            const logout=await page.request.post(`${base}/api/auth/logout`,{headers:{Origin:base}});
            assert.equal(logout.status(),204);
            await page.goto(`${base}/workspaces`);
            await page.getByRole('button',{name:'登录',exact:true}).waitFor();
            assert.equal(new URL(page.url()).searchParams.get('next'),'/workspaces');
            await page.getByLabel('用户名',{exact:true}).fill('浏览器Agent');
            await page.getByLabel('密码',{exact:true}).fill('Isolated-Browser-Test-2026!');
            await page.getByRole('button',{name:'登录',exact:true}).click();
            await page.getByRole('table').waitFor();
            assert.equal(new URL(page.url()).pathname,'/workspaces');
            await page.route('**/api/workspaces?*',route=>route.fulfill({status:401,body:'{}'}));
            await page.getByRole('button',{name:'刷新列表',exact:true}).click();
            await page.getByRole('link',{name:'前往登录',exact:true}).waitFor();
            assert.equal(await page.getByText('还没有工作空间',{exact:true}).count(),0);
        });
    }
    console.log(`Workspace list ${mode}: ${count} passed`);
} finally {await browser.close();}
