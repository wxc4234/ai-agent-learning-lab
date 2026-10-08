import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const output = fileURLToPath(new URL("../../../output/playwright/workbench-product/", import.meta.url));

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || "playwright");
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    page.setDefaultTimeout(8000);
    const requests = [], errors = [], checks = [], held = [];
    const created_at = "2026-10-08T00:00:00Z";
    const projects = [{ external_id: "a".repeat(32), name: "项目甲", created_at }, { external_id: "e".repeat(32), name: "项目乙", created_at }];
    const tasks = [
        { external_id: "b".repeat(32), workspace_id: projects[0].external_id, conversation_id: "1".repeat(32), title: "任务甲", created_at },
        { external_id: "c".repeat(32), workspace_id: projects[0].external_id, conversation_id: "2".repeat(32), title: "任务乙", created_at },
        { external_id: "f".repeat(32), workspace_id: projects[1].external_id, conversation_id: "3".repeat(32), title: "另一项目任务", created_at },
    ];
    let holdChanges = false;
    page.on("pageerror", error => errors.push(error.message));
    await mkdir(output, { recursive: true });
    const changes = () => page.getByRole("complementary", { name: "文件改动", exact: true });
    const open = () => page.getByRole("button", { name: "查看改动", exact: true });
    const input = () => page.getByLabel("你的问题");
    async function noInternalControls() {
        // 核对DOM，而不是仅看折叠后不可见；内部组件没有挂载到产品树。
        for (const text of ["高级详情", "运行记录与诊断", "代码参考", "快照技术信息", "检索技术信息", "查找代码", "读取代码快照"])
            assert.equal(await page.getByText(text, { exact: true }).count(), 0, text);
        assert.equal(await page.getByLabel("项目更多操作", { exact: true }).count(), 0);
        assert.equal(await page.getByRole("region", { name: "代码快照选择", exact: true }).count(), 0);
        assert.equal(await page.getByRole("region", { name: "代码查询预览", exact: true }).count(), 0);
    }
    try {
        await page.route("**/api/**", async route => {
            const request = route.request(), url = new URL(request.url()), path = url.pathname;
            requests.push({ method: request.method(), path });
            assert.equal(request.method(), "GET", "navigation and viewing must not mutate or send to models");
            assert.ok(!/code-embedding-batches|code-query-context|sample-status|cleanup-preflight|\/executions/.test(path), path);
            if (path === "/api/workspaces") { await route.fulfill({ json: { items: projects, has_more: false } }); return; }
            const match = /\/workspaces\/([a-f0-9]+)\/tasks(?:\/([a-f0-9]+))?/.exec(path);
            if (match) {
                const [, workspace, task] = match;
                if (!task) { await route.fulfill({ json: { items: tasks.filter(item => item.workspace_id === workspace), next_cursor: null } }); return; }
                if (path.endsWith("/messages")) { await route.fulfill({ json: { messages: [] } }); return; }
                // 现有对话用量恢复保留，此只读请求不属于诊断面板挂载。
                if (path.endsWith("/runs")) { await route.fulfill({ json: { workspace_id: workspace, task_id: task, items: [], next_cursor: null } }); return; }
                if (path.endsWith("/file-edit-proposals")) {
                    const data = { workspace_id: workspace, task_id: task, items: [], next_cursor: null };
                    if (holdChanges) {
                        held.push(() => route.fulfill({ json: { ...data, items: [{ proposal_id: "d".repeat(32), relative_path: "late-old-task.py",
                            status: "pending", application_status: "idle", diff_truncated: false }] } }).catch(() => {})); return;
                    }
                    await route.fulfill({ json: data }); return;
                }
                if (path.endsWith(task)) { await route.fulfill({ json: { workspace: projects.find(item => item.external_id === workspace), task: tasks.find(item => item.external_id === task) } }); return; }
            }
            // 其他文件操作不在本次改动范围，拒绝漏拦截，不伪造成功。
            await route.fulfill({ status: 503, json: { code: "isolated_unavailable" } });
        });
        await page.goto(`${base}/?workspace=${projects[0].external_id}&task=${tasks[0].external_id}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
        await input().fill("保留对话草稿"); await noInternalControls();
        assert.equal(await page.locator("#workbench-details").isVisible(), false);
        assert.equal(requests.filter(item => item.path.endsWith("/file-edit-proposals")).length, 0);
        checks.push("no advanced/retrieval/diagnostic entry or mounted hidden component / changes default closed");
        await page.getByRole("button", { name: "收起导航", exact: true }).click();
        await page.getByRole("button", { name: "展开导航", exact: true }).click();
        assert.equal(await input().inputValue(), "保留对话草稿");
        checks.push("navigation toggle preserves chat draft");
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: `${output}/default-${width}.png`, fullPage: true });
        }
        checks.push("1366/1920 desktop product layout");
        await open().focus(); await page.keyboard.press("Enter"); await changes().getByText("当前没有修改提案", { exact: true }).waitFor();
        assert.ok(await page.getByRole("button", { name: "关闭详情", exact: true }).evaluate(element => element === document.activeElement));
        await noInternalControls();
        checks.push("keyboard opens actual file changes / focus lands on close / no internal controls");
        const separator = page.getByRole("separator", { name: "调整运行详情宽度" });
        const widthBefore = Number(await separator.getAttribute("aria-valuenow"));
        await separator.focus(); await page.keyboard.press("ArrowLeft");
        assert.ok(Number(await separator.getAttribute("aria-valuenow")) > widthBefore);
        assert.equal(await input().inputValue(), "保留对话草稿");
        await page.getByRole("button", { name: "关闭详情", exact: true }).press("Escape");
        assert.equal(await page.locator("#workbench-details").isVisible(), false);
        assert.ok(await open().evaluate(element => element === document.activeElement));
        assert.equal(await input().inputValue(), "保留对话草稿");
        checks.push("resize / Escape close / focus restoration retain chat draft");
        await open().click(); await changes().getByText("当前没有修改提案", { exact: true }).waitFor();
        holdChanges = true;
        await changes().getByRole("button", { name: "刷新改动", exact: true }).click();
        for (let index = 0; held.length === 0 && index < 80; index++) await page.waitForTimeout(10);
        assert.equal(held.length, 1);
        holdChanges = false;
        await page.getByRole("button", { name: "任务乙", exact: true }).click();
        await changes().getByText("当前没有修改提案", { exact: true }).waitFor(); await held.shift()();
        await page.waitForTimeout(80);
        assert.equal(await changes().getByText("late-old-task.py", { exact: true }).count(), 0);
        assert.ok(requests.filter(item => item.path.endsWith("/file-edit-proposals")).at(-1).path.includes(tasks[1].external_id));
        await noInternalControls(); checks.push("task switch preserves file changes scope and rejects late old task result");
        await page.getByRole("button", { name: "项目乙", exact: true }).click();
        await page.getByRole("button", { name: "另一项目任务", exact: true }).click();
        await changes().getByText("当前没有修改提案", { exact: true }).waitFor();
        assert.ok(requests.filter(item => item.path.endsWith("/file-edit-proposals")).at(-1).path.includes(projects[1].external_id));
        await noInternalControls(); checks.push("workspace switch keeps file changes in current scope");
        await page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 }); await input().waitFor();
        assert.equal(await page.locator("#workbench-details").isVisible(), false); await noInternalControls();
        assert.deepEqual(errors, []); assert.ok(requests.every(item => item.method === "GET"));
        checks.push("reload does not restore internal entries / no model, mutation or diagnostic traffic / no pageerror");
        await writeFile(`${output}/evidence.json`, JSON.stringify({ result: "passed", checks, viewports: [1366, 1920], mutations: 0,
            boundary: "actual ChatPanel/WorkbenchShell/WorkspaceSidebar/TaskChangesPanel with production styles and controlled BFF responses; no real Next/API/database/model integration; no chat send or file mutation" }, null, 4));
        console.log(`PASS product workbench: ${checks.length} flows; 1366/1920; internal controls absent; no mutations`);
    } catch (error) {
        console.error({ pageErrors: errors }); console.error((await page.locator("body").innerText()).slice(0, 5000));
        await page.screenshot({ path: `${output}/failure.png`, fullPage: true }); throw error;
    } finally { await browser.close(); }
}
