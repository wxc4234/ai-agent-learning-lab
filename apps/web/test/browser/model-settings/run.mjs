import { createRequire } from "node:module";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import { verify } from "./verify.mjs";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const temporary = await mkdtemp(path.join(tmpdir(), "agent-model-settings-"));
const webpack = require("next/dist/compiled/webpack")().webpack;
const postcss = createRequire(require.resolve("@tailwindcss/postcss"))("postcss");
const plugin = require("@tailwindcss/postcss");
let server;
try {
    // 编译实际ChatPanel/导航/详情与生产样式；不在Next应用增加测试路由。
    const compiler = webpack({
        mode: "development", devtool: false, context: root,
        entry: path.join(root, "test/browser/model-settings/entry.tsx"),
        output: { path: temporary, filename: "bundle.js" },
        // Next依赖的浏览器环境替换只在隔离编译内模拟，不注入宿主环境/密钥。
        plugins: [new webpack.DefinePlugin({ "process.env": "{}" })],
        resolve: { extensions: [".tsx", ".ts", ".js"], alias: { "@": path.join(root, "src") } },
        module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(root, "test/browser/execution-component/typescript-loader.cjs") }] },
    });
    await new Promise((resolve, reject) => compiler.run((error, stats) => {
        compiler.close(() => {});
        if (error || stats.hasErrors()) reject(error ?? Error(stats.toString({ all: false, errors: true })));
        else resolve();
    }));
    const cssPath = path.join(root, "src/app/globals.css");
    const css = await postcss([plugin({ base: root })]).process(await readFile(cssPath, "utf8"), { from: cssPath });
    await writeFile(path.join(temporary, "style.css"), css.css);
    server = http.createServer(async (request, response) => {
        // 漏拦截的API请求拒绝，不能接触本机业务数据库或真实供应商。
        if (request.url.startsWith("/api/")) { response.writeHead(503); response.end("isolated fixture"); return; }
        const filename = request.url === "/bundle.js" ? "bundle.js" : request.url === "/style.css" ? "style.css" : null;
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("Content-Type", filename === "bundle.js" ? "text/javascript" : filename ? "text/css" : "text/html; charset=utf-8");
        response.end(filename ? await readFile(path.join(temporary, filename)) : '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>产品工作台隔离验收</title><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>');
    });
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    await verify(`http://127.0.0.1:${server.address().port}`);
} finally {
    if (server?.listening) await new Promise(resolve => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
}
