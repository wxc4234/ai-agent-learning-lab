import assert from 'node:assert/strict';
import { readFile, writeFile, rename, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/project-write-assessment-integration/', import.meta.url));
const fixtures = JSON.parse(await readFile(output + 'fixtures.json', 'utf8'));
const [normal, changed, foreign] = fixtures;
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(60000);
const errors = [], requests = [], classifications = [];
let backendAssessments = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => requests.push({ method: request.method(), path: new URL(request.url()).pathname }));
const endpoint = item => `${base}/api/workspaces/${item.workspace_id}/tasks/${item.task_id}/file-edit-proposals/${item.proposal_id}`;
const button = () => page.getByRole('button', { name: '检查写入条件', exact: true });
const region = () => page.getByRole('region', { name: '写入条件检查' });
const count = () => requests.filter(r => r.path.endsWith('/assessment')).length;
async function open(item) {
    await page.goto(`${base}/?workspace=${item.workspace_id}&task=${item.task_id}`);
    await page.getByRole('button', { name: '查看改动', exact: true }).click();
    const pane = page.getByRole('complementary', { name: '文件改动', exact: true });
    await pane.locator('summary').filter({ hasText: 'example.txt' }).click();
    await pane.getByRole('button', { name: '查看提案详情', exact: true }).press('Enter');
    await pane.locator('summary').filter({ hasText: '普通项目写入许可' }).press('Enter');
    return pane;
}
async function query() {
    await page.getByRole('button', { name: '查询许可状态', exact: true }).click();
    await button().waitFor();
}
async function assertReceipt(response, item, result) {
    assert.equal(response.status(), 200, await response.text());
    assert.equal(response.headers()['cache-control'], 'no-store');
    assert.equal(response.headers()['set-cookie'], undefined);
    assert.deepEqual(await response.json(), {
        workspace_id: item.workspace_id, task_id: item.task_id, proposal_id: item.proposal_id, result,
    });
    classifications.push(result);
}
async function assess(item, result, label) {
    const responsePromise = page.waitForResponse(response => response.url() === endpoint(item) + '/write-grant/assessment');
    await button().press('Enter');
    const response = await responsePromise;
    backendAssessments++;
    await assertReceipt(response, item, result);
    await region().getByText(`检查时未满足写入条件：${label}。`, { exact: true }).waitFor();
}
async function explicitAssessment(item, body, status, code) {
    const response = await page.request.post(endpoint(item) + '/write-grant/assessment', { headers: { Origin: base }, data: body });
    backendAssessments++;
    assert.equal(response.status(), status, await response.text());
    if (status === 200) await assertReceipt(response, item, code);
    else assert.equal((await response.json()).code, code);
}
// 所有故障注入都先等待真实BFF/API检查完成，仅控制回执到达浏览器的时机。
async function holdReceipt(item) {
    let complete, release;
    const ready = new Promise(resolve => { complete = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    const handler = async route => {
        const response = await route.fetch({ maxRetries: 0, maxRedirects: 0 });
        backendAssessments++;
        await assertReceipt(response, item, 'exclusive_access_unconfirmed');
        complete();
        await gate;
        await route.fulfill({ response }).catch(() => {});
    };
    await page.route('**/write-grant/assessment', handler, { times: 1 });
    await button().click(); await ready;
    return release;
}
try {
    const grants = {};
    for (const item of [normal, changed]) {
        const pane = await open(item);
        const approvalResponse = page.waitForResponse(response => response.url() === endpoint(item) + '/decision');
        await pane.getByRole('button', { name: '批准提案', exact: true }).press('Enter');
        const approval = await approvalResponse;
        const approvalText = await approval.text();
        await writeFile(output + 'approval-diagnostic.json', JSON.stringify({ status: approval.status(), body: approvalText }, null, 4));
        assert.equal(approval.status(), 200, approvalText);
        await pane.getByText('已确认批准此提案。应用状态以执行结果为准。', { exact: true }).waitFor();
        await pane.getByRole('button', { name: '查询许可状态', exact: true }).click();
        await pane.getByRole('button', { name: '发放本提案许可', exact: true }).click();
        await pane.getByRole('button', { name: '确认发放许可', exact: true }).click();
        await button().waitFor();
        const response = await page.request.get(endpoint(item) + '/write-grant');
        assert.equal(response.status(), 200);
        grants[item.marker] = (await response.json()).grant;
    }
    assert.equal(count(), 0);
    await open(normal); await query();
    await assess(normal, 'exclusive_access_unconfirmed', '无法确认文件的排他访问条件');
    await assess(normal, 'exclusive_access_unconfirmed', '无法确认文件的排他访问条件');
    await explicitAssessment(normal, { grant_id: grants.normal.grant_id, revision: 2, apply_requested: true }, 200, 'grant_changed');
    await explicitAssessment(normal, { grant_id: grants.normal.grant_id, revision: 1, apply_requested: false }, 200, 'apply_not_requested');
    await explicitAssessment(foreign, { grant_id: grants.normal.grant_id, revision: 1, apply_requested: true }, 404, 'workspace_not_accessible');
    await page.route('**/write-grant/assessment', async route => {
        const response = await route.fetch({ maxRetries: 0, maxRedirects: 0 });
        backendAssessments++;
        await assertReceipt(response, normal, 'exclusive_access_unconfirmed');
        await route.abort('failed');
    }, { times: 1 });
    await button().click(); await region().getByRole('alert').waitFor();
    assert.ok((await region().innerText()).includes('当前结果未知'));
    const beforeReload = count(); await open(normal); await query();
    assert.equal(count(), beforeReload);
    assert.equal(await region().getByRole('status').count(), 0);
    let release = await holdReceipt(normal);
    await page.getByRole('button', { name: '取消检查', exact: true }).click();
    release();
    await region().getByText('检查已取消，当前结果未知。', { exact: true }).waitFor();
    await assess(normal, 'exclusive_access_unconfirmed', '无法确认文件的排他访问条件');
    release = await holdReceipt(normal);
    const beforeSwitch = count(); await open(changed); release(); await query();
    assert.equal(count(), beforeSwitch);
    assert.equal(await region().getByRole('status').count(), 0);
    await assess(changed, 'exclusive_access_unconfirmed', '无法确认文件的排他访问条件');
    // 只修改隔离夹具登记的目标；同字节新inode与内容变化分别验证。
    const previous = await stat(changed.file, { bigint: true });
    await writeFile(changed.file + '.replacement', 'old\n');
    await rename(changed.file + '.replacement', changed.file);
    assert.notEqual((await stat(changed.file, { bigint: true })).ino, previous.ino);
    await assess(changed, 'target_changed', '目标文件或目录绑定已变化');
    await writeFile(changed.file, 'external edit\n');
    const failure = page.waitForResponse(response => response.url() === endpoint(changed) + '/write-grant/assessment');
    await button().click(); const failed = await failure; backendAssessments++;
    assert.equal(failed.status(), 500, await failed.text());
    assert.equal((await failed.json()).code, 'project_write_assessment_read_failed');
    await region().getByRole('alert').waitFor();
    assert.ok((await region().innerText()).includes('当前结果未知'));
    await open(normal); await query();
    // 既有首次旧修订撤销404问题仅记录本轮观察，不据一次成功宣称修复。
    const stale = await page.request.post(endpoint(normal) + '/write-grant/revoke', {
        headers: { Origin: base }, data: { grant_id: grants.normal.grant_id, revision: 2 },
    });
    const staleBody = await stale.json();
    assert.equal(stale.status(), 409, JSON.stringify(staleBody));
    assert.equal(staleBody.code, 'project_write_grant_conflict');
    await page.getByRole('button', { name: '撤销本提案许可', exact: true }).click();
    await page.getByRole('button', { name: '确认撤销许可', exact: true }).press('Enter');
    await page.getByText('查询时许可：已撤销，不可重新启用', { exact: true }).waitFor();
    await assess(normal, 'grant_revoked', '许可已撤销');
    for (const width of [1366, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: output + `${width}.png`, fullPage: true });
    }
    assert.equal(requests.filter(r => r.path.endsWith('/apply')).length, 0);
    assert.equal(requests.filter(r => r.path.includes('/chat')).length, 0);
    const mutations = requests.filter(r => r.method === 'POST' && !r.path.endsWith('/assessment'));
    assert.equal(mutations.length, 5); // 两次批准、两次发放、一次撤销，全部是显式夹具准备。
    assert.deepEqual(errors, []);
    const finalFiles = {};
    for (const item of fixtures) {
        const info = await stat(item.file, { bigint: true });
        finalFiles[item.marker] = { sha256: createHash('sha256').update(await readFile(item.file)).digest('hex'),
            inode: String(info.ino), mtime_ns: String(info.mtimeNs), size: Number(info.size) };
    }
    await writeFile(output + 'browser.json', JSON.stringify({ backend_assessments: backendAssessments,
        grants, final_files: finalFiles, classifications, no_apply: true, no_model: true,
        stale_revoke: { status: stale.status(), code: staleBody.code },
        transport_faults: ['real response dropped', 'real response held across cancel', 'real response held across resource switch'],
    }, null, 4));
    console.log(`PASS real assessment PC/BFF/API: ${backendAssessments} requests; normal/revision/revoke/inode/content/ownership, lost receipt/cancel/switch/reload, PC 1366/1920`);
} catch (error) {
    // 失败也保留当前UI和错误，避免只剩定位器超时而丢失现场。
    await page.screenshot({ path: output + 'failure.png', fullPage: true }).catch(() => {});
    await writeFile(output + 'failure.json', JSON.stringify({ error: String(error),
        text: await page.locator('body').innerText().catch(() => ''), requests, backendAssessments,
    }, null, 4));
    throw error;
} finally { await browser.close(); }
