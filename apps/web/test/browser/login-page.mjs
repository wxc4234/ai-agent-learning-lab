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
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    try {
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        await run(page, context);
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
    await scenario('registration: PC layout and keyboard at laptop and desktop sizes', async (page) => {
        await mkdir('/private/tmp/agent-ui-preview', {recursive:true});
        for (const [width,height] of [[1366,768],[1920,1080]]) {
            await page.setViewportSize({width,height});
            await page.goto(`${base}/login`);
            await form(page).waitFor();
            await page.screenshot({path:`/private/tmp/agent-ui-preview/login-pc-${width}.png`,fullPage:true});
            await page.getByRole('button',{name:'没有账号？注册账号'}).click();
            await page.getByLabel('用户名',{exact:true}).focus();
            await page.keyboard.press('Tab');
            assert.equal(await page.getByLabel('密码',{exact:true}).evaluate(el => el === document.activeElement),true);
            await page.keyboard.press('Tab');
            assert.equal(await page.getByLabel('确认密码',{exact:true}).evaluate(el => el === document.activeElement),true);
            const bounds = await page.getByRole('region').boundingBox();
            assert.ok(bounds);
            assert.ok(Math.abs(bounds.x + bounds.width / 2 - width / 2) < 2);
            assert.ok(bounds.width >= 400 && bounds.width <= 500);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
            const button = await page.getByRole('button',{name:'注册',exact:true}).boundingBox();
            assert.ok(button && button.y >= 0 && button.y + button.height <= height);
            await page.screenshot({path:`/private/tmp/agent-ui-preview/register-pc-${width}.png`,fullPage:true});
        }
    });
    await scenario('registration: create account, duplicate, login and workspace HTTP', async (page, context) => {
        await page.goto(`${base}/login?next=/`);
        await page.getByRole('button', {name:'没有账号？注册账号'}).click();
        await page.getByLabel('用户名', {exact:true}).fill('注册浏览器Agent');
        await page.getByLabel('密码', {exact:true}).fill('Registration-Test-2026!');
        await page.getByLabel('确认密码', {exact:true}).fill('different');
        await page.getByRole('button', {name:'注册',exact:true}).click();
        await page.getByText('两次输入的密码不一致。').waitFor();
        await page.getByLabel('确认密码', {exact:true}).fill('Registration-Test-2026!');
        await mkdir('/private/tmp/agent-ui-preview', {recursive:true});
        await page.screenshot({path:'/private/tmp/agent-ui-preview/register.png', fullPage:true});
        await page.getByRole('button', {name:'注册',exact:true}).click();
        await page.getByText('注册成功，请使用新账号登录。').waitFor();
        assert.equal(await page.getByLabel('用户名',{exact:true}).inputValue(),'注册浏览器agent');
        assert.equal(await page.getByLabel('密码',{exact:true}).inputValue(),'');
        assert.equal((await context.cookies()).some(cookie => cookie.name === 'agent_session'),false);
        await page.getByRole('button', {name:'没有账号？注册账号'}).click();
        await page.getByLabel('密码',{exact:true}).fill('Registration-Test-2026!');
        await page.getByLabel('确认密码',{exact:true}).fill('Registration-Test-2026!');
        await page.getByRole('button',{name:'注册',exact:true}).click();
        await page.getByText('用户名已被使用，请更换用户名或登录。').waitFor();
        await page.getByRole('button',{name:'已有账号？返回登录'}).click();
        await page.getByLabel('密码',{exact:true}).fill('Registration-Test-2026!');
        await form(page).click();
        await page.getByLabel('你的问题').waitFor();
        const result = await page.evaluate(async () => {
            const response = await fetch('/api/workspaces', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:'  注册后的工作空间  '}),
            });
            return {status:response.status,body:await response.json()};
        });
        assert.equal(result.status,201);
        assert.equal(result.body.name,'注册后的工作空间');
        assert.deepEqual(Object.keys(result.body).sort(),['created_at','external_id','name']);
    });
    await scenario('registration: validation and uncertain result keep login available', async (page) => {
        await page.goto(`${base}/login`);
        await page.getByRole('button',{name:'没有账号？注册账号'}).click();
        await page.getByLabel('用户名',{exact:true}).fill('注册校验用户');
        await page.getByLabel('密码',{exact:true}).fill('short');
        await page.getByLabel('确认密码',{exact:true}).fill('short');
        await page.getByRole('button',{name:'注册',exact:true}).click();
        await page.getByText('注册信息不符合要求，请检查用户名和密码。').waitFor();
        await page.route('**/api/auth/register', route => route.fulfill({status:504,body:'{}'}));
        await page.getByLabel('密码',{exact:true}).fill('Registration-Test-2026!');
        await page.getByLabel('确认密码',{exact:true}).fill('Registration-Test-2026!');
        await page.getByRole('button',{name:'注册',exact:true}).click();
        await page.getByText('注册结果尚未确认。如果账号已创建，可尝试登录。').waitFor();
        assert.equal(await page.getByLabel('密码',{exact:true}).inputValue(),'');
        await page.getByRole('button',{name:'已有账号？返回登录'}).click();
        await form(page).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    });
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
    await scenario('real login, wrong password, reload, logout, PC layout', async (page, context) => {
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
    async function loginAndStartSlow(page, prompt = '[cancel-test] 停止测试') {
        await page.goto(`${base}/login`);
        await form(page).waitFor();
        await fill(page);
        await form(page).click();
        await logged(page).waitFor();
        await page.goto(base);
        await page.getByLabel('你的问题').fill(prompt);
        await page.getByRole('button', {name:'发送', exact:true}).click();
        const number = page.getByText(/^运行编号：/);
        await number.waitFor();
        return (await number.innerText()).match(/\d+/)[0];
    }

    async function timeline(context, runId, cookie) {
        const response = await context.request.get(`http://127.0.0.1:18000/runs/${runId}`, {
            headers: {Cookie:`agent_session=${cookie.value}`},
        });
        assert.equal(response.status(), 200);
        return response.json();
    }

    await scenario('run cancel real BFF owner stop and repeated cancel', async (page, context) => {
        const runId = await loginAndStartSlow(page);
        const cookie = (await context.cookies()).find(c => c.name === 'agent_session');
        const response = page.waitForResponse(r => r.url().endsWith(`/runs/${runId}/cancel`));
        await page.getByRole('button', {name:'停止生成', exact:true}).click();
        assert.equal((await response).status(), 204);
        await page.getByText('已停止生成', {exact:true}).waitFor();
        assert.equal(await page.locator('main [role="alert"]').count(), 0);
        const before = await timeline(context, runId, cookie);
        assert.equal(before.status, 'aborted');
        const repeat = await context.request.post(`${base}/api/runs/${runId}/cancel`, {
            headers:{Origin:base}, data:{reason:'timeout'},
        });
        assert.equal(repeat.status(), 204);
        assert.deepEqual(await timeline(context, runId, cookie), before);
        assert.equal(before.events.filter(e => e.event_type === 'RUN_CANCELLATION_REQUESTED').length, 1);
    });

    for (const mode of ['401', '404', '503', 'network', 'timeout']) {
        await scenario(`run cancel ${mode} shows independent notice and stops locally`, async (page, context) => {
            const runId = await loginAndStartSlow(page);
            const ownerCookie = (await context.cookies()).find(c => c.name === 'agent_session');
            if (mode === '401') {
                assert.equal((await context.request.post(`${base}/api/auth/logout`, {headers:{Origin:base}})).status(), 204);
                await context.addCookies([ownerCookie]);
            } else if (mode === '404') {
                assert.equal((await context.request.post(`${base}/api/auth/login`, {
                    headers:{Origin:base}, data:{username:'浏览器用户乙',password:'Isolated-Browser-Test-2026!'},
                })).status(), 200);
            } else {
                await page.route('**/api/runs/*/cancel', async route => {
                    if (mode === 'network') return route.abort();
                    if (mode === 'timeout') {
                        await new Promise(resolve => setTimeout(resolve, 5500));
                    }
                    await route.fulfill({status:503, body:'simulated broker failure'}).catch(() => {});
                });
            }
            await page.getByRole('button', {name:'停止生成', exact:true}).click();
            const notice = page.locator('main [role="alert"]');
            await notice.waitFor();
            assert.match(await notice.innerText(), mode === '401' ? /登录状态已失效/ : mode === '404' ? /运行不存在或不可访问/ : /取消.*未完成|取消服务暂时不可用/);
            await page.getByText('已停止生成', {exact:true}).waitFor();
            assert.equal(await page.getByLabel('你的问题').inputValue(), '[cancel-test] 停止测试');
            if (mode === '404') {
                const state = await timeline(context, runId, ownerCookie);
                assert.equal(state.events.some(e => e.event_type === 'RUN_CANCELLATION_REQUESTED'), false);
            }
        });
    }

    await scenario('run cancel delayed failure does not overwrite next request', async page => {
        let release;
        const pending = new Promise(resolve => { release = resolve; });
        let started;
        const entered = new Promise(resolve => { started = resolve; });
        await page.route('**/api/runs/*/cancel', async route => {
            started();
            await pending;
            await route.fulfill({status:503, body:'late failure'}).catch(() => {});
        });
        await loginAndStartSlow(page, '[cancel-short] 延迟测试');
        await page.getByRole('button', {name:'停止生成', exact:true}).click();
        await entered;
        await page.getByText('已停止生成', {exact:true}).waitFor();
        await page.getByLabel('你的问题').fill('新一轮正常问题');
        await page.getByRole('button', {name:'发送', exact:true}).click();
        await page.getByText('隔离模型：认证聊天成功。', {exact:true}).waitFor();
        const late = page.waitForResponse(r => r.url().includes('/cancel'));
        release();
        await late;
        await page.waitForTimeout(100);
        assert.equal(await page.locator('main [role="alert"]').count(), 0);
        await page.getByText('已完成', {exact:true}).waitFor();
    });
    console.log(`Browser scenarios: ${count} passed`);
} finally { await browser.close(); }
