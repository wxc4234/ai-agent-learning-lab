import { createRequire } from 'node:module';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import { verify } from './verify.mjs';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const temporary = await mkdtemp(path.join(tmpdir(), 'agent-execution-ui-'));
const webpack = require('next/dist/compiled/webpack')().webpack;
const postcss = createRequire(require.resolve('@tailwindcss/postcss'))('postcss');
const plugin = require('@tailwindcss/postcss');
let server;
try {
    // 独立编译真实组件及依赖，不往Next生产路由添加测试入口。
    const compiler = webpack({
        mode: 'development', devtool: false, context: root,
        entry: path.join(root, 'test/browser/execution-component/entry.tsx'),
        output: { path: temporary, filename: 'bundle.js' },
        resolve: { extensions: ['.tsx', '.ts', '.js'], alias: { '@': path.join(root, 'src') } },
        module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(root, 'test/browser/execution-component/typescript-loader.cjs') }] },
    });
    await new Promise((resolve, reject) => compiler.run((error, stats) => {
        compiler.close(() => {});
        if (error || stats.hasErrors()) reject(error ?? Error(stats.toString({ all: false, errors: true })));
        else resolve();
    }));
    const cssPath = path.join(root, 'src/app/globals.css');
    const css = await postcss([plugin({ base: root })]).process(await readFile(cssPath, 'utf8'), { from: cssPath });
    await writeFile(path.join(temporary, 'style.css'), css.css);
    server = http.createServer(async (req, res) => {
        // 即使浏览器拦截失效，也只返回拒绝，不代理任何应用请求。
        if (req.url.startsWith('/api/')) { res.writeHead(503); res.end('isolated fixture'); return; }
        const filename = req.url === '/bundle.js' ? 'bundle.js' : req.url === '/style.css' ? 'style.css' : null;
        res.setHeader('Cache-Control', 'no-store');
        res.setHeader('Content-Type', filename === 'bundle.js' ? 'text/javascript' : filename ? 'text/css' : 'text/html; charset=utf-8');
        res.end(filename ? await readFile(path.join(temporary, filename)) : '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>样例应用隔离测试</title><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>');
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    await verify(`http://127.0.0.1:${server.address().port}`);
} finally {
    if (server?.listening) await new Promise(resolve => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
}
