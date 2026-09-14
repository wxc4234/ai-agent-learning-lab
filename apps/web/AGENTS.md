<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## 项目 UI 约定

- 基础控件优先复用 `src/components/ui/` 的 shadcn/ui 组件；当前已有 Button、Input、Textarea、Label、Card。
- 颜色使用 `globals.css` 中的语义主题（background、foreground、primary、muted、border 等），保持系统明暗模式一致，避免在业务页重复基础控件样式。
- UI 配套维护不改变课程顺序；认证、流式状态、取消与资源授权按原课程分别推进。
