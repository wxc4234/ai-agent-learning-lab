# Agent Web

`apps/web` 是 AI Agent 产品的 Next.js 前端，也是浏览器与 FastAPI 之间的 BFF（Backend for Frontend）。

## 本地启动

先在项目根目录启动 Docker Compose 和 FastAPI；完整步骤见仓库根目录的 [ENVIRONMENT.md](../../ENVIRONMENT.md)。然后运行：

```bash
pnpm install --frozen-lockfile
pnpm dev
```

打开 <http://127.0.0.1:3000>。

## 环境变量

默认情况下，BFF 路由代理到本机 FastAPI：`http://127.0.0.1:8000`。

后端地址变化时，复制 `.env.example` 为 `.env.local`，再修改：

```dotenv
API_BASE_URL=http://127.0.0.1:8000
```

不要使用 `NEXT_PUBLIC_API_BASE_URL`：`API_BASE_URL` 只在 Next.js 服务端 Route Handler 中读取，浏览器无法看到。

## 当前接口边界

浏览器调用 `POST /api/chat/stream`；`src/app/api/chat/stream/route.ts` 将请求体和 FastAPI 的流式响应透明转发。不要在这个路由中调用 `response.json()`，否则会读取完整正文并破坏流式显示。
