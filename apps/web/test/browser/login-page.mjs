import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTH_TEST_BASE_URL;
if (!base) throw new Error('Run through the isolated authentication test launcher.');
const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}),
});
let count = 0;
async function scenario(name, run) {
    if (process.env.AUTH_TEST_FILTER && !name.includes(process.env.AUTH_TEST_FILTER)) return;
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    try {
        await run(await context.newPage(), context);
        count += 1;
        console.log(`PASS: ${name}`);
    } finally { await context.close(); }
}
const form = page => page.getByRole('button', { name: '登录', exact: true });
const retry = page => page.getByRole('button', { name: '重新查询登录状态', exact: true });
const logged = page => page.getByRole('button', { name: '退出登录', exact: true });
async function fill(page, password = 'Isolated-Browser-Test-2026!') {
    await page.getByLabel('用户名', { exact: true }).fill('浏览器Agent');
    await page.getByLabel('密码', { exact: true }).fill(password);
}
try {
    await scenario('shared UI controls and light-dark themes', async (page) => {
        await mkdir('/private/tmp/agent-ui-preview', {recursive:true});
        for (const scheme of ['light','dark']) {
            await page.emulateMedia({colorScheme:scheme});
            await page.goto(`${base}/login`);
            await form(page).waitFor();
            assert.equal(await form(page).getAttribute('data-slot'),'button');
            assert.equal(await page.getByLabel('用户名',{exact:true}).getAttribute('data-slot'),'input');
            await page.screenshot({path:`/private/tmp/agent-ui-preview/login-${scheme}.png`,fullPage:true});
        }
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        await page.goto(base);
        await page.getByLabel('你的问题').waitFor();
        const send = page.getByRole('button',{name:'发送',exact:true});
        assert.equal(await send.isDisabled(),true);
        await page.getByLabel('你的问题').fill('UI test');
        assert.equal(await send.isEnabled(),true);
        await page.route('**/api/chat/stream', route => route.fulfill({status:500,body:'test failure'}));
        await send.click();
        await page.getByRole('button',{name:'重试',exact:true}).waitFor();
        assert.equal(await page.getByRole('main').getByRole('alert').count(),1);
        for (const scheme of ['light','dark']) {
            await page.emulateMedia({colorScheme:scheme});
            await page.screenshot({path:`/private/tmp/agent-ui-preview/chat-${scheme}.png`,fullPage:true});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
        }
    });
    await scenario('real login, wrong password, reload, logout, mobile layout', async (page, context) => {
        await page.goto(`${base}/login`);
        await form(page).waitFor();
        await fill(page, 'wrong');
        await form(page).click();
        await page.getByText('用户名或密码错误', { exact: false }).waitFor();
        assert.equal(await page.getByLabel('密码', { exact: true }).inputValue(), '');
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        assert.ok((await context.cookies()).find(c => c.name === 'agent_session')?.httpOnly);
        assert.equal(await page.evaluate(() => document.cookie.includes('agent_session')), false);
        await page.reload();
        await logged(page).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await logged(page).click();
        await form(page).waitFor();
        assert.equal((await context.cookies()).some(c => c.name === 'agent_session'), false);
        assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    });
    for (const failure of ['server', 'network', 'invalid identity']) {
        await scenario(`query ${failure} yields unknown and can recover`, async page => {
            await page.route('**/api/auth/me', route => failure === 'network'
                ? route.abort()
                : route.fulfill({status: failure === 'server' ? 500 : 200, json: {username: 12}}));
            await page.goto(`${base}/login`);
            await retry(page).waitFor();
            assert.equal(await form(page).count(), 0);
            await page.unroute('**/api/auth/me');
            await retry(page).click();
            await form(page).waitFor();
        });
    }
    await scenario('422 clears password and allows correction', async page => {
        await page.route('**/api/auth/login', route => route.fulfill({status:422,json:{code:'invalid_login_input'}}));
        await page.goto(`${base}/login`);
        await fill(page);
        await form(page).click();
        await page.getByText('登录信息不符合要求', {exact:false}).waitFor();
        assert.equal(await page.getByLabel('密码', {exact:true}).inputValue(),'');
        assert.equal(await form(page).isEnabled(),true);
    });
    await scenario('same-tick repeated submit sends once and preserves password whitespace', async page => {
        let calls = 0;
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        await page.route('**/api/auth/login', async route => {
            calls += 1;
            assert.equal(route.request().postDataJSON().password, ' untouched ');
            await gate;
            await route.fulfill({status:401,json:{code:'invalid_credentials'}});
        });
        await page.goto(`${base}/login`);
        await fill(page, ' untouched ');
        await page.locator('form').evaluate(form => {
            form.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}));
            form.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}));
        });
        await page.getByRole('button', {name:'正在登录…',exact:true}).waitFor();
        assert.equal(await page.getByLabel('密码',{exact:true}).isDisabled(),true);
        release();
        await page.getByText('用户名或密码错误',{exact:false}).waitFor();
        assert.equal(calls,1);
    });
    for (const operation of ['login', 'logout']) {
        await scenario(`${operation} failure requires reconciliation`, async page => {
            await page.goto(`${base}/login`);
            if (operation === 'logout') {
                await fill(page);
                await form(page).click();
                await logged(page).waitFor();
            }
            await page.route(`**/api/auth/${operation}`, route => route.fulfill({status:500,json:{}}));
            if (operation === 'login') {
                await fill(page);
                await form(page).click();
            } else { await logged(page).click(); }
            await retry(page).waitFor();
            await retry(page).click();
            await (operation === 'login' ? form(page) : logged(page)).waitFor();
        });
    }
    await scenario('browser deadline releases pending state', async page => {
        await page.addInitScript(() => {
            const original = AbortSignal.timeout.bind(AbortSignal);
            AbortSignal.timeout = ms => original(ms === 15000 ? 100 : ms);
            const fetchOriginal = window.fetch.bind(window);
            window.fetch = (input, init) => String(input) === '/api/auth/me'
                ? new Promise((resolve, reject) => {
                    init.signal.addEventListener('abort', () => reject(init.signal.reason), {once:true});
                }) : fetchOriginal(input, init);
        });
        await page.goto(`${base}/login`);
        await retry(page).waitFor();
        assert.equal(await retry(page).isEnabled(),true);
    });
    await scenario('unmount aborts request; late old response cannot overwrite new mount', async page => {
        await page.addInitScript(() => {
            const original = window.fetch.bind(window);
            AbortSignal.timeout = () => new AbortController().signal;
            window.authProbe = {calls:0,aborted:false};
            window.fetch = (input, init) => {
                if (String(input) !== '/api/auth/me') return original(input, init);
                window.authProbe.calls += 1;
                if (window.authProbe.calls > 1) {
                    if (location.pathname === '/') return Promise.resolve(Response.json({external_id:'test',username:'test'}));
                    return original(input, init);
                }
                init.signal.addEventListener('abort', () => { window.authProbe.aborted = true; });
                return new Promise(resolve => {
                    window.authProbe.release = () => resolve(Response.json({external_id:'old',username:'stale-user'}));
                });
            };
        });
        await page.goto(`${base}/login`);
        await page.waitForFunction(() => window.authProbe.calls === 1);
        await page.getByRole('link',{name:'返回聊天首页'}).click();
        await page.waitForURL(base + '/');
        await page.waitForFunction(() => window.authProbe.aborted);
        assert.equal(await page.evaluate(() => window.authProbe.aborted),true);
        await page.getByRole('link',{name:'登录测试页'}).click();
        await form(page).waitFor();
        await page.evaluate(async () => {
            window.authProbe.release();
            await new Promise(resolve => setTimeout(resolve, 0));
        });
        assert.equal(await form(page).isVisible(),true);
        assert.equal(await page.getByText('stale-user').count(),0);
    });
    await scenario('gate real unauthenticated login-return, refresh, account and logout', async (page) => {
        await page.goto(base);
        await page.waitForURL(`${base}/login?next=%2F`);
        await form(page).waitFor();
        assert.equal(await page.getByLabel('你的问题').count(),0);
        await fill(page);
        await form(page).click();
        await page.waitForURL(base + '/');
        await page.getByLabel('你的问题').waitFor();
        await page.reload();
        await page.getByLabel('你的问题').waitFor();
        await page.getByRole('link',{name:'账号与退出',exact:true}).click();
        await page.waitForURL(base + '/login');
        await logged(page).waitFor();
        await logged(page).click();
        await form(page).waitFor();
        await page.getByRole('link',{name:'返回聊天首页'}).click();
        await page.waitForURL(`${base}/login?next=%2F`);
        await form(page).waitFor();
    });
    for (const failure of ['server','network','invalid identity']) {
        await scenario(`gate ${failure} keeps home closed and retry can redirect`, async page => {
            await page.route('**/api/auth/me', route => failure === 'network' ? route.abort()
                : route.fulfill({status: failure === 'server' ? 500 : 200,json:{username:12}}));
            await page.goto(base);
            await retry(page).waitFor();
            assert.equal(new URL(page.url()).pathname,'/');
            assert.equal(await page.getByLabel('你的问题').count(),0);
            await page.unroute('**/api/auth/me');
            await retry(page).click();
            await page.waitForURL(`${base}/login?next=%2F`);
            await form(page).waitFor();
        });
    }
    await scenario('gate pending hides chat; retry succeeds without duplicate checks', async page => {
        let release;
        const gate = new Promise(resolve => {release=resolve;});
        let calls = 0;
        await page.route('**/api/auth/me', async route => {
            calls += 1;
            await gate;
            await route.fulfill({status:500,json:{}});
        });
        await page.goto(base);
        await page.getByRole('heading',{name:'正在确认登录状态',exact:true}).waitFor();
        assert.equal(await page.getByLabel('你的问题').count(),0);
        release();
        await retry(page).waitFor();
        assert.equal(calls,1);
        await page.unroute('**/api/auth/me');
        let retries = 0;
        await page.route('**/api/auth/me', route => {
            retries += 1;
            return route.fulfill({status:200,json:{external_id:'mock',username:'test'}});
        });
        await retry(page).evaluate(button => {button.click();button.click();});
        await page.getByLabel('你的问题').waitFor();
        assert.equal(retries,1);
    });
    await scenario('gate browser timeout leaves retry available', async page => {
        await page.addInitScript(() => {
            const original = AbortSignal.timeout.bind(AbortSignal);
            AbortSignal.timeout = ms => original(ms === 15000 ? 100 : ms);
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => String(input) === '/api/auth/me'
                ? new Promise((resolve,reject) => init.signal.addEventListener('abort',() => reject(init.signal.reason),{once:true}))
                : originalFetch(input,init);
        });
        await page.goto(base);
        await retry(page).waitFor();
        assert.equal(new URL(page.url()).pathname,'/');
        assert.equal(await page.getByLabel('你的问题').count(),0);
    });
    await scenario('gate unmount aborts and ignores late unauthorized response', async page => {
        await page.addInitScript(() => {
            AbortSignal.timeout = () => new AbortController().signal;
            const original = window.fetch.bind(window);
            window.gateProbe = {aborted:false};
            window.fetch = (input,init) => {
                if (String(input) !== '/api/auth/me' || location.pathname !== '/') return original(input,init);
                init.signal.addEventListener('abort',() => {window.gateProbe.aborted=true;});
                return new Promise(resolve => {window.gateProbe.release = () => resolve(new Response(null,{status:401}));});
            };
        });
        await page.goto(base);
        await page.waitForFunction(() => typeof window.gateProbe.release === 'function');
        await page.getByRole('link',{name:'登录测试页',exact:true}).click();
        await form(page).waitFor();
        await page.waitForFunction(() => window.gateProbe.aborted);
        await page.evaluate(async () => {
            window.gateProbe.release();
            await new Promise(resolve => setTimeout(resolve,0));
        });
        assert.equal(page.url(),base + '/login');
    });
    await scenario('gate return target is fixed and rejects unsafe or duplicate next', async page => {
        await page.goto(`${base}/login`);
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        for (const query of ['next=https%3A%2F%2Fevil.test','next=%2F%2Fevil.test','next=javascript%3Aalert(1)','next=%2Flogin','next=%2F&next=%2F','next=%252F']) {
            await page.goto(`${base}/login?${query}`);
            await logged(page).waitFor();
            assert.equal(page.url(),`${base}/login?${query}`);
        }
        await page.goto(`${base}/login?next=%2F`);
        await page.waitForURL(base + '/');
        await page.getByLabel('你的问题').waitFor();
    });
    await scenario('chat real login BFF stream and revoked session rejection', async (page, context) => {
        await page.goto(`${base}/login`);
        await form(page).waitFor();
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        const cookie = (await context.cookies()).find(c => c.name === 'agent_session');
        assert.ok(cookie);
        await page.goto(base);
        await page.getByLabel('你的问题').fill('认证聊天验收');
        const firstResponse = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByRole('button', {name:'发送',exact:true}).click();
        const response = await firstResponse;
        assert.equal(response.status(), 200);
        assert.match(response.headers()['content-type'], /application\/x-ndjson/);
        assert.match(response.headers()['x-run-id'], /^[1-9]\d*$/);
        assert.equal(response.headers()['cache-control'], 'no-store');
        await page.getByText('隔离模型：认证聊天成功。', {exact:true}).waitFor();
        await page.getByRole('button', {name:'发送',exact:true}).waitFor({state:'visible'});
        // Revoke on another request while the mounted gate remains open; replay the
        // original cookie to ensure FastAPI rejects a revoked database session.
        const logout = await context.request.post(`${base}/api/auth/logout`, {headers:{Origin:base}});
        assert.equal(logout.status(), 204);
        await context.addCookies([cookie]);
        await page.getByLabel('你的问题').fill('撤销后不得创建运行');
        const rejectedResponse = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByRole('button', {name:'发送',exact:true}).click();
        const rejected = await rejectedResponse;
        assert.equal(rejected.status(), 401);
        assert.equal(rejected.headers()['x-run-id'], undefined);
        await page.getByText('登录状态已失效，请打开“账号与退出”重新登录。', {exact:true}).waitFor();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '撤销后不得创建运行');
    });
    await scenario('ownership other account cannot reuse session through BFF', async (page, context) => {
        await page.goto(`${base}/login`);
        await form(page).waitFor();
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        await page.goto(base);
        await page.getByLabel('你的问题').fill('甲的会话');
        const firstResponse = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByRole('button', {name:'发送',exact:true}).click();
        const first = await firstResponse;
        assert.equal(first.status(), 200);
        const sessionId = first.request().postDataJSON().session_id;
        await page.getByText('隔离模型：认证聊天成功。', {exact:true}).waitFor();
        const loginB = await context.request.post(`${base}/api/auth/login`, {
            headers:{Origin:base}, data:{username:'浏览器用户乙',password:'Isolated-Browser-Test-2026!'},
        });
        assert.equal(loginB.status(), 200);
        await page.route('**/api/chat/stream', async route => {
            const body = route.request().postDataJSON();
            await route.continue({postData:JSON.stringify({...body, session_id:sessionId})});
        });
        await page.getByLabel('你的问题').fill('乙尝试沿用甲的标识');
        const deniedResponse = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByRole('button', {name:'发送',exact:true}).click();
        const denied = await deniedResponse;
        assert.equal(denied.status(), 404);
        assert.equal(denied.headers()['x-run-id'], undefined);
        await page.getByText('会话不存在或不可访问，请重新发送问题以创建新会话。', {exact:true}).waitFor();
        assert.equal(await page.getByLabel('你的问题').inputValue(), '乙尝试沿用甲的标识');
        await page.unroute('**/api/chat/stream');
        await page.getByLabel('你的问题').fill('乙的新会话');
        const ownResponse = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByRole('button', {name:'发送',exact:true}).click();
        assert.equal((await ownResponse).status(), 200);
        await page.getByText('隔离模型：认证聊天成功。', {exact:true}).waitFor();
    });
    console.log(`Browser scenarios: ${count} passed`);
} finally { await browser.close(); }
