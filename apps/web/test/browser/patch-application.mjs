import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = '/private/tmp/agent-ui-patch-application/output/playwright';
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
if (!base || process.env.BROWSER_APP_MODE !== 'local') throw new Error('Requires isolated local launcher');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(30000);
const errors = [], report = [];
let chats = 0, applies = 0;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chat/stream')) chats++;
    if (request.method() === 'POST' && request.url().endsWith('/apply')) applies++;
});
try {
    for (const fixture of fixtures) {
        const { marker, workspace_id: workspace, task_id: task } = fixture;
        const file = `${fixture.root}/example.txt`;
        await page.goto(`${base}/?workspace=${workspace}&task=${task}`);
        await page.getByLabel('你的问题').fill(`[patch-apply-${marker}] 请保存补丁提案`);
        const stream = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
        await page.getByLabel('你的问题').press('Enter');
        const response = await stream;
        assert.equal(response.status(), 200);
        const runId = response.headers()['x-run-id'];
        await page.getByText('补丁提案已保存，请审阅后决定是否批准', { exact: true }).waitFor();
        const expand = page.getByRole('button', { name: '展开详情', exact: true });
        if (await expand.isVisible()) await expand.click();
        const card = page.getByRole('region', { name: '文件修改提案回执', exact: true });
        await card.waitFor();
        const run = await (await page.request.get(`${base}/api/runs/${runId}`)).json();
        const receipt = JSON.parse(run.events.find(e => e.event_type === 'TOOL_CALL_RESULT').payload.result);
        const endpoint = `${base}/api/workspaces/${workspace}/tasks/${task}/file-edit-proposals/${receipt.proposal_id}`;
        const panel = page.getByRole('region', { name: '文件修改提案详情', exact: true });
        await panel.getByRole('button', { name: '查看提案详情', exact: true }).press('Enter');
        await panel.getByLabel('提案 Diff', { exact: true }).waitFor();
        assert.deepEqual(await readFile(file), Buffer.from('old\n'));
        if (marker === 'pending' || marker === 'truncated') {
            if (marker === 'truncated') assert.ok(await panel.getByRole('button', { name: '批准提案', exact: true }).isDisabled());
            const rejected = await page.request.post(`${endpoint}/apply`, { headers: { Origin: base }, data: { action: 'apply' } });
            assert.notEqual(rejected.status(), 200);
            if (marker === 'truncated') {
                const approve = await page.request.post(`${endpoint}/decision`, { headers: { Origin: base }, data: { decision: 'approved' } });
                assert.notEqual(approve.status(), 200);
            }
            assert.deepEqual(await readFile(file), Buffer.from('old\n'));
        } else {
            const decision = page.waitForResponse(r => r.url() === `${endpoint}/decision`);
            await panel.getByRole('button', { name: '批准提案', exact: true }).press('Enter');
            assert.equal((await decision).status(), 200);
            assert.deepEqual(await readFile(file), Buffer.from('old\n'));
            if (marker === 'conflict') await writeFile(file, 'external\n');
            await page.goto(`${base}/sample-apply?workspace=${workspace}&task=${task}&proposal=${receipt.proposal_id}`);
            const action = page.getByRole('region', { name: '受限样例提案应用', exact: true });
            await action.getByRole('checkbox').waitFor();
            assert.ok(await action.getByRole('button', { name: '应用样例提案', exact: true }).isDisabled());
            await action.getByRole('checkbox').check();
            const applied = page.waitForResponse(r => r.url() === `${endpoint}/apply`);
            await action.getByRole('button', { name: '应用样例提案', exact: true }).press('Enter');
            const appliedResponse = await applied;
            assert.equal(appliedResponse.status(), 200);
            const result = await appliedResponse.json();
            assert.equal(result.application_status, marker === 'success' ? 'applied' : 'not_applied');
            assert.equal(result.file_status, marker === 'success' ? 'replaced' : 'not_attempted');
            assert.equal(appliedResponse.headers()['cache-control'], 'no-store');
            assert.deepEqual(await readFile(file), Buffer.from(marker === 'success' ? 'new\n' : 'external\n'));
            if (marker === 'success') assert.equal(createHash('sha256').update(await readFile(file)).digest('hex'), receipt.proposed_sha256);
            assert.ok(await action.getByRole('button', { name: '应用样例提案', exact: true }).isDisabled());
        }
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/${marker}-${width}.png` });
        }
        const before = { chats, applies, file: await readFile(file), stat: await stat(file) };
        if (marker === 'success' || marker === 'conflict') {
            await page.reload();
            const button = page.getByRole('button', { name: '应用样例提案', exact: true });
            await button.waitFor();
            assert.ok(await button.isDisabled());
        }
        await page.goto(`${base}/?workspace=${workspace}&task=${task}`);
        await page.getByText('补丁提案已保存，请审阅后决定是否批准', { exact: true }).waitFor();
        const history = page.getByRole('button', { name: '展开详情', exact: true });
        if (await history.isVisible()) await history.click();
        await page.getByRole('button', { name: `查看运行 ${runId}`, exact: true }).click();
        await panel.getByRole('button', { name: '查看提案详情', exact: true }).click();
        await panel.getByLabel('提案 Diff', { exact: true }).waitFor();
        await panel.getByRole('button', { name: '查询应用状态', exact: true }).click();
        const status = await (await page.request.get(`${endpoint}/application-status`)).json();
        assert.equal(status.application_status, marker === 'success' ? 'applied' : marker === 'conflict' ? 'not_applied' : 'idle');
        await panel.getByText(`查询时应用状态：${{ success: '已登记应用成功', conflict: '已登记本次未应用', pending: '尚未领取执行', truncated: '尚未领取执行' }[marker]}`, { exact: true }).waitFor();
        assert.equal(chats, before.chats);
        assert.equal(applies, before.applies);
        assert.deepEqual(await readFile(file), before.file);
        assert.equal((await stat(file)).ino, before.stat.ino);
        assert.equal((await stat(file)).mtimeMs, before.stat.mtimeMs);
        report.push({ marker, proposal_id: receipt.proposal_id, run_id: runId, status: status.application_status, refresh_no_replay: true });
        console.log(`PASS ${marker}: real patch, decision, application, file and history`);
    }
    assert.deepEqual(errors, []);
    assert.equal(chats, 4);
    assert.equal(applies, 2);
    await writeFile(`${output}/evidence.json`, JSON.stringify(report, null, 4));
} catch (error) {
    await page.screenshot({ path: `${output}/failure.png` }).catch(() => {});
    throw error;
} finally { await browser.close(); }
