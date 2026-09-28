import { openAdvancedDetails } from './workbench-navigation.mjs';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = fileURLToPath(new URL('../../output/playwright/coding-loop/', import.meta.url));
const fixtures = JSON.parse(await readFile(`${output}/fixtures.json`, 'utf8'));
const base = process.env.AUTH_TEST_BASE_URL;
assert.ok(base && process.env.BROWSER_APP_MODE === 'local');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.setDefaultTimeout(60000);
let chats = 0, applies = 0, decisions = 0;
const errors = [], evidence = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', r => {
    if (r.method() !== 'POST') return;
    if (r.url().endsWith('/api/chat/stream')) chats++;
    if (r.url().endsWith('/apply')) applies++;
    if (r.url().endsWith('/decision')) decisions++;
});
async function send(prompt, answer) {
    await page.getByLabel('你的问题').fill(prompt);
    const pending = page.waitForResponse(r => r.url().endsWith('/api/chat/stream'));
    await page.getByLabel('你的问题').press('Enter');
    const response = await pending;
    assert.equal(response.status(), 200);
    await page.getByText(answer, { exact: true }).waitFor();
    const id = response.headers()['x-run-id'];
    const run = await (await page.request.get(`${base}/api/runs/${id}`)).json();
    return { id, results: run.events.filter(e => e.event_type === 'TOOL_CALL_RESULT').map(e => JSON.parse(e.payload.result)) };
}
async function fingerprint(file) {
    const info = await stat(file);
    return [createHash('sha256').update(await readFile(file)).digest('hex'), info.ino, info.mtimeMs];
}
async function review() {
    const panel = page.getByRole('region', { name: '文件修改提案详情', exact: true });
    await panel.getByRole('button', { name: '查看提案详情', exact: true }).press('Enter');
    await panel.getByLabel('提案 Diff', { exact: true }).waitFor();
    return panel;
}
try {
    for (const { marker, workspace_id, task_id, root } of fixtures) {
        const file = `${root}/example.txt`;
        await page.goto(`${base}/?workspace=${workspace_id}&task=${task_id}`);
        assert.equal(await page.getByRole('complementary', { name: '高级详情', exact: true }).count(), 0);
        const proposal = await send('[coding-loop-propose] 保存补丁', '补丁提案已保存，请审阅后决定是否批准');
        const receipt = proposal.results[0];
        const endpoint = `${base}/api/workspaces/${workspace_id}/tasks/${task_id}/file-edit-proposals/${receipt.proposal_id}`;
        await openAdvancedDetails(page);
        let panel = await review();
        assert.equal(await panel.getByText('应用到受控样例', { exact: true }).count(), 0);
        const pendingDecision = page.waitForResponse(r => r.url() === `${endpoint}/decision`);
        await panel.getByRole('button', { name: marker === 'rejected' ? '拒绝提案' : '批准提案', exact: true }).press('Enter');
        assert.equal((await pendingDecision).status(), 200);
        assert.equal(await readFile(file, 'utf8'), 'old\n');
        if (marker !== 'rejected') {
            if (marker === 'stale') await writeFile(file, 'external\n');
            await panel.getByText('应用到受控样例', { exact: true }).click();
            const action = panel.getByRole('region', { name: '受限样例提案应用', exact: true });
            await action.getByRole('checkbox').waitFor();
            assert.ok(await action.getByRole('button', { name: '应用样例提案', exact: true }).isDisabled());
            await action.getByRole('checkbox').check();
            const pendingApply = page.waitForResponse(r => r.url() === `${endpoint}/apply`);
            await action.getByRole('button', { name: '应用样例提案', exact: true }).press('Enter');
            const response = await pendingApply;
            assert.equal(response.status(), 200);
            const result = await response.json();
            assert.equal(result.application_status, marker === 'approved' ? 'applied' : 'not_applied');
            assert.equal(result.file_status, marker === 'approved' ? 'replaced' : 'not_attempted');
        } else {
            assert.equal(await panel.getByText('应用到受控样例', { exact: true }).count(), 0);
            const rejected = await page.request.post(`${endpoint}/apply`, { headers: { Origin: base }, data: { action: 'apply' } });
            assert.equal(rejected.status(), 409);
            assert.equal((await rejected.json()).code, 'proposal_not_approved');
        }
        assert.equal(await readFile(file, 'utf8'), { approved: 'new\n', rejected: 'old\n', stale: 'external\n' }[marker]);
        const before = await fingerprint(file);
        const check = await send('[coding-loop-check] 读取差异并运行固定验证', '差异与固定验证已记录。');
        assert.equal(check.results.length, 2);
        const [diff, verification] = check.results;
        assert.equal(diff.source, 'task_application_sample');
        assert.equal(diff.content_sha256, before[0]);
        assert.equal(diff.diff === '', marker === 'rejected');
        assert.equal(verification.outcome, marker === 'approved' ? 'passed' : 'failed');
        assert.equal(verification.report_status, 'complete');
        assert.deepEqual(await fingerprint(file), before);
        await page.getByLabel('应用样例差异', { exact: true }).waitFor();
        await page.getByLabel('受控样例验证', { exact: true }).getByText(marker === 'approved' ? '验证通过' : '验证失败', { exact: true }).waitFor();
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/${marker}-${width}.png` });
        }
        const counts = [chats, applies, decisions];
        await page.reload();
        await page.getByText('差异与固定验证已记录。', { exact: true }).waitFor();
        await openAdvancedDetails(page);
        await page.getByRole('button', { name: `查看运行 ${check.id}`, exact: true }).press('Enter');
        await page.getByLabel('应用样例差异', { exact: true }).waitFor();
        await page.getByLabel('受控样例验证', { exact: true }).waitFor();
        await page.getByRole('button', { name: '返回列表', exact: true }).press('Enter');
        await page.getByRole('button', { name: `查看运行 ${proposal.id}`, exact: true }).press('Enter');
        panel = await review();
        if (marker !== 'rejected') {
            await panel.getByText('应用到受控样例', { exact: true }).click();
            const button = panel.getByRole('button', { name: '应用样例提案', exact: true });
            await button.waitFor();
            assert.ok(await button.isDisabled());
        }
        assert.deepEqual([chats, applies, decisions], counts);
        assert.deepEqual(await fingerprint(file), before);
        assert.equal(await page.getByText('查看执行详情', { exact: true }).count(), 0);
        evidence.push({ marker, proposal_id: receipt.proposal_id, diff, verification, history_no_replay: true });
        console.log(`PASS ${marker}: real proposal/application/Git/Docker, desktop, history no replay`);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual([chats, applies, decisions], [6, 2, 3]);
    await writeFile(`${output}/evidence.json`, JSON.stringify(evidence, null, 4));
} catch (error) {
    console.error(await page.locator('body').innerText());
    await page.screenshot({ path: `${output}/failure.png` });
    throw error;
} finally { await browser.close(); }
