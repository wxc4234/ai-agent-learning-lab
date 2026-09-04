# 前端

全栈底座之一（你的出身优势项）。面试通常**从项目切入**来问（如「流式输出怎么实现」「停止生成发生了什么」），很少脱离上下文单独考八股；但知识点仍需体系化掌握，才能在换项目、换问法时接得住。

准备重点：抓与 **AI 前端 / Agent UI** 强相关的原理（流式 / 事件循环 / 浏览器渲染 / 网络 / 性能），传统 UI 八股（CSS 花活、手写组件库）可少放。

## 主题分类

- `js/` JavaScript 核心（原型链/闭包/作用域/事件循环/Promise/异步）
- `ts/` TypeScript（类型系统/泛型/类型体操/工程化）
- `css/` CSS（布局/层叠/盒模型/响应式/BFC）
- `framework/` 框架（Vue 3 / React 生态、响应式、虚拟 DOM、状态管理）
- `browser/` 浏览器（渲染流程/缓存/存储/安全/CORS）
- `network/` 网络（HTTP/HTTPS/HTTP2/WebSocket/SSE）
- `engineering/` 工程化（构建/打包/CI/规范/部署）
- `performance/` 性能优化（首屏/渲染/内存/监控）
- `agent-ui/` Agent 前端（流式渲染/事件协议/状态机，与 `LEARNING_CURRICULUM.md` 联动）

## 题解模板

```markdown
# 题目

> 主题：xxx | 频率：高/中/低 | 关联项目：xxx

## 考点

## 核心答案（讲取舍）

## 结合项目怎么讲

## 追问清单

## 延伸 / 坑
```

## 题目索引

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| - | （示例）闭包 | 高 | 待做 |

<!-- ORGANIZED-INTERVIEW-IMPORT:START -->

## 已整理题目

| 主题 | 题目 | 频率 | 状态 |
| --- | --- | --- | --- |
| agent-ui | [SSE 回包频率很高时，如何控制流式数据的消费与渲染节奏？](agent-ui/stream-render-backpressure.md) | 高 | 已写 |
| agent-ui | [一条 SSE 交叉返回 A/B 两个答案分支时，如何保证数据归属、完成状态和用户选择互不污染？](agent-ui/ab-answer-consistency.md) | 高 | 已写 |
| agent-ui | [上一轮仍在生成时发起新提问或重新回答，如何避免 SSE 串流和旧数据污染？](agent-ui/chatsearch-request-isolation.md) | 高 | 已写 |
| agent-ui | [服务端动态下发画布组件时，前端如何完成协议转换、动态渲染和宿主能力隔离？](agent-ui/dynamic-canvas-components.md) | 高 | 已写 |
| agent-ui | [服务端长期快于前端渲染时，如何限制队列增长并保证内容完整？](agent-ui/stream-queue-overload.md) | 高 | 已写 |
| agent-ui | [用户点击发送到第一段 AI 内容真正可见，前端经历了哪些关键步骤？](agent-ui/chatsearch-main-flow.md) | 高 | 已写 |
| agent-ui | [画布组件未抛出渲染完成事件时，如何避免整个流式消费队列永久阻塞？](agent-ui/render-finished-watchdog.md) | 高 | 已写 |
| browser | [AI 返回的 Markdown 如何防止 XSS，同时保留必要的富文本能力？](browser/markdown-xss.md) | 高 | 已写 |
| browser | [San `nextTick` 与 `requestAnimationFrame` 分别保证了什么，为什么测量布局和执行滚动时不能只用 `nextTick`？](browser/san-nexttick-raf.md) | 高 | 已写 |
| browser | [`ResizeObserver` 回调再次改变元素尺寸时，浏览器如何处理反馈循环，项目中如何避免循环触发？](browser/resizeobserver-feedback-loop.md) | 高 | 已写 |
| browser | [动态组件如何限制脚本来源，并处理版本兼容、失败降级和灰度回滚？](browser/dynamic-component-security.md) | 中 | 已写 |
| css | [纵向 Flex 对话页为什么会被长内容撑开，如何让滚动只发生在回答区？](css/flex-scroll-layout.md) | 高 | 已写 |
| engineering | [ChatSearch 为什么从 Rollup 迁移到 Vite/Rolldown，迁移带来了哪些收益？](engineering/vite-rolldown-migration.md) | 高 | 已写 |
| engineering | [PC 和 Wise 如何复用核心逻辑，同时避免多端差异散落在业务代码中？](engineering/multi-platform-code-reuse.md) | 高 | 已写 |
| engineering | [monorepo 中如何划分跨包职责，并避免一次环境适配破坏其他端？](engineering/monorepo-package-boundaries.md) | 高 | 已写 |
| engineering | [多端构建为什么使用 `advancedChunks`，如何平衡首屏体积、缓存和请求数量？](engineering/vite-chunk-splitting.md) | 高 | 已写 |
| engineering | [如何使用 Codex 完成近百个文件的组件迁移，并控制 Agent 的改动风险？](engineering/ai-assisted-component-migration.md) | 中 | 已写 |
| engineering | [旧 HTML 引用的 hash chunk 被清理导致白屏，如何保证发布一致性？](engineering/vite-hash-chunk-deploy.md) | 高 | 已写 |
| engineering | [自定义 Vite 插件导致启动和构建变慢，如何定位与优化？](engineering/vite-plugin-performance.md) | 高 | 已写 |
| framework | [React EventBus 订阅为什么会重复，并读取到旧的 `queryId`？](framework/react-eventbus-stale-closure.md) | 高 | 已写 |
| js | [EventBus 如何根据 `from/to` 决定事件走 H5 本地通信还是 Native 跨端通信？](js/eventbus-channel-routing.md) | 高 | 已写 |
| js | [用 TypeScript 实现支持 `on/off/once/emit` 的 EventEmitter](js/event-emitter.md) | 高 | 已写 |
| js | [连续微任务为什么会导致流式页面掉帧，如何主动让出主线程？](js/microtask-starvation.md) | 高 | 已写 |
| network | [ReadableStream 返回的 chunk 为什么不能直接当成完整 SSE 消息，项目如何正确拆包？](network/sse-chunk-parser.md) | 高 | 已写 |
| network | [SSE 连接中断后如何处理，断点续传如何避免数据重复或丢失？](network/sse-checkpoint-reconnect.md) | 高 | 已写 |
| network | [Wise 输入框在 Native、ChatSearch 在 WebView，两端的 `search` 事件是如何通信并进入对话链路的？](network/native-webview-search-channel.md) | 高 | 已写 |
| network | [为什么项目选择基于 `fetch + ReadableStream` 的 SSE，而不是原生 `EventSource` 或 WebSocket？](network/sse-vs-websocket-eventsource.md) | 高 | 已写 |
| performance | [AI 内容持续增长时，如何在自动跟随和用户主动滚动之间正确切换？](performance/streaming-auto-scroll.md) | 高 | 已写 |
| performance | [Hybrid WebView 如何在首屏速度、BaseData 新鲜度和失败降级之间做权衡？](performance/hybrid-basedata-startup.md) | 高 | 已写 |
| performance | [后端持续生成，但前端长时间无数据后突然批量到达，如何分层定位？](performance/sse-layered-debugging.md) | 高 | 已写 |
| performance | [长会话内存持续增长时，如何定位泄漏并统一治理资源生命周期？](performance/memory-leak-lifecycle.md) | 高 | 已写 |
| performance | [首屏 JS 体积下降，但线上 LCP 没有改善，如何分层定位？](performance/js-size-lcp-debugging.md) | 高 | 已写 |
| ts | [用 TypeScript 可辨识联合建模不同 SSE 数据包](ts/sse-discriminated-union.md) | 高 | 已写 |

<!-- ORGANIZED-INTERVIEW-IMPORT:END -->
