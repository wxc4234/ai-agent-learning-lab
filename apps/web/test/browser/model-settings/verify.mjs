import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const output = fileURLToPath(new URL("../../../output/playwright/model-settings/", import.meta.url));

export async function verify(base) {
    const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || "playwright");
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE });
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [], checks = [], writes = [];
    page.on("pageerror", e => errors.push(e.message));
    const channel = { enabled: true, base_url: "https://fixture.invalid/v1", model: "chat-fixture", dimensions: null, request_dimensions: false, key_configured: true, source: "environment" };
    let data = { revision: "a".repeat(64), chat: { ...channel }, embedding: { ...channel, enabled: false, model: "embed-fixture", dimensions: null } };
    let failSave = false;
    try {
        await mkdir(output, { recursive: true });
        await page.route("**/api/**", async route => {
            const req = route.request(), path = new URL(req.url()).pathname;
            if (path === "/api/workspaces") return route.fulfill({ json: { items: [], has_more: false } });
            if (path === "/api/model-settings/detect-dimensions") {
                writes.push({ kind: "detect", body: req.postDataJSON() });
                return route.fulfill({ json: { dimensions: 1024 } });
            }
            if (path === "/api/model-settings") {
                if (req.method() === "PUT") {
                    const body = req.postDataJSON();
                    writes.push({ kind: "save", body });
                    if (failSave) return route.fulfill({ status: 409, json: { message: "conflict" } });
                    const { api_key, ...config } = body.config;
                    data = { ...data, revision: "b".repeat(64), [body.channel]: { ...config, key_configured: !!api_key || data[body.channel].key_configured, source: "local" } };
                }
                return route.fulfill({ json: data });
            }
            return route.fulfill({ status: 503, json: {} });
        });
        await page.goto(base);
        const trigger = page.getByRole("button", { name: "模型设置", exact: true });
        await trigger.focus(); await page.keyboard.press("Enter");
        const dialog = page.getByRole("dialog", { name: "模型设置", exact: true });
        await dialog.getByLabel("模型名称", { exact: true }).waitFor();
        assert.equal(await dialog.getByLabel("向量维度", { exact: true }).count(), 0);
        assert.equal(await dialog.getByLabel("API Key", { exact: true }).inputValue(), "");
        checks.push("sidebar keyboard entry / chat has no dimension / key not returned");
        await dialog.getByLabel("模型名称", { exact: true }).fill("my-chat");
        await dialog.getByLabel("API Key", { exact: true }).fill("fake-browser-key");
        await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
        await dialog.getByText("已保存，下一次请求生效。", { exact: true }).waitFor();
        assert.equal(await dialog.getByLabel("API Key", { exact: true }).inputValue(), "");
        assert.equal(writes[0].body.config.dimensions, null);
        checks.push("chat save / key cleared from form");
        await dialog.getByRole("button", { name: "代码检索模型", exact: true }).click();
        await dialog.getByLabel("启用代码检索模型", { exact: true }).check();
        await dialog.getByLabel("服务地址", { exact: true }).fill("https://api.deepseek.com");
        await dialog.getByText("此 DeepSeek 地址用于聊天，请选择支持 Embedding 的服务。", { exact: true }).waitFor();
        assert.ok(await dialog.getByRole("button", { name: "检测维度", exact: true }).isDisabled());
        await dialog.getByLabel("服务地址", { exact: true }).fill("https://fixture.invalid/v1");
        await dialog.getByRole("button", { name: "检测维度", exact: true }).click();
        await dialog.getByText("已检测到模型默认输出维度；点击保存后生效。", { exact: true }).waitFor();
        assert.equal(await dialog.getByLabel("向量维度", { exact: true }).inputValue(), "1024");
        assert.equal(writes.filter(w => w.kind === "save").length, 1);
        await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
        await dialog.getByText("已保存，下一次请求生效。", { exact: true }).waitFor();
        checks.push("wrong provider explained / detect separate from save / dimension persisted");
        for (const width of [1366, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.screenshot({ path: output + `/settings-${width}.png`, fullPage: true });
        }
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "hidden" });
        assert.ok(await trigger.evaluate(el => el === document.activeElement));
        await trigger.click();
        await page.getByRole("button", { name: "代码检索模型", exact: true }).click();
        await page.getByLabel("向量维度", { exact: true }).waitFor();
        assert.equal(await page.getByLabel("向量维度", { exact: true }).inputValue(), "1024");
        failSave = true;
        await page.getByRole("button", { name: "保存配置", exact: true }).click();
        await page.getByText("配置已变化，请关闭后重新打开。", { exact: true }).waitFor();
        checks.push("PC widths / Escape and focus / reopen persisted / conflict not success");
        assert.ok(!(await page.evaluate(() => JSON.stringify(localStorage))).includes("fake-browser-key"));
        assert.deepEqual(errors, []);
        await writeFile(output + "/evidence.json", JSON.stringify({ checks, pageErrors: errors, realProviderRequests: 0 }, null, 4));
        console.log(JSON.stringify({ checks, output }));
    } finally { await browser.close(); }
}
