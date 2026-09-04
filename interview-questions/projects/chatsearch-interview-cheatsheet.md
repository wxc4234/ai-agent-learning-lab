# ChatSearch：面试速查

## 1. 项目总收口

> 这个项目有价值的地方不是简单使用了 SSE、离线包或状态机，而是解决了复杂异步链路中的一致性与性能冲突。我的处理方式不是假设事件会按理想顺序发生，而是先定义数据归属、完成条件和系统不变量，再让正常、停止、重连和异常路径收敛到明确状态。

| 问题 | 核心处理 |
|---|---|
| 页面从 URL 到 DOM 如何出现 | HTML 启动数据＋根组件/路由骨架＋首屏业务分支＋回答增量渲染 |
| AI 回答采用什么流式协议 | 基于 Fetch 的 SSE，复用 HTTP 体系 |
| 多个回答分支异步结束 | 稳定路由＋双重完成屏障＋幂等收口 |
| 新旧请求回调交叉 | 回调绑定稳定 QAPair id，不依赖当前下标 |
| 网络速度快于 DOM | 消费队列＋组件 ack＋应用层背压 |
| checkpoint 语义不严格 | 当前明确为 best-effort；严格方案使用连续业务提交水位 |
| Hybrid 静态资源与动态数据冲突 | 离线包＋BaseData SWR＋初始化屏障＋发送前恢复 |
| 大型项目构建和首屏资源膨胀 | Vite/Rolldown＋依赖分层＋按需加载与精准预载 |

## 2. 每个难点统一怎么讲

使用四段式，不要一开始就报字段名：

```text
场景：什么业务动作触发了问题
矛盾：原来的两个目标为什么冲突
方案：我如何拆状态、定不变量、处理异常
结果：问题如何验证，仍有哪些边界
```

可以对应到 STAR：

```text
S：业务场景和原方案的问题
T：需要同时保证哪些目标
A：字段、状态、判断顺序和异常收敛
R：线上指标、问题量或稳定性变化
```

## 3. 代码实现索引

以下路径相对于仓库 `baidu/ps-se-fe-tpl/chat-search`。面试开场不需要报路径，面试官问“具体在哪里”时再使用。

| 主题 | 主要实现位置 | 职责 |
|---|---|---|
| HTML 壳与 BaseData | `packages/chat-search/search.html`、`search-pc.html`、`packages/chat-util/util/get-data-form-dom.ts` | 提供 `#app`、读取 head 中的 JSON 启动数据 |
| 启动与路由 | `packages/chat-search/src/client/pages/chat-search*/bootstrap.ts`、`component/chat-container/index*.san`、`service/route.ts` | attach 根容器，根据平台和 URL 挂载 Home/Chat |
| 首屏业务分支 | `packages/chat-search/src/client/component/chat-container/chat-init*.ts` | 选择引导内容、历史、query 自动请求或 frame 预加载 |
| ChatBody 组件树 | `packages/chat-search/src/client/service/chatstream.ts`、`packages/chat-stream/src/components/chat-body/index.ts` | 注入业务组件，把 QAPair 映射为 Question/Answer |
| Answer 实例复用 | `packages/chat-stream/src/components/item-render/assistant-render-manager.ts`、`packages/chat-search/src/client/component/chat-answer/index.ts` | 复用 Answer，只处理新增 `answerData` |
| SSE Fetch 封装 | `packages/chat-sse/src/fetch-event-source/fetch.ts` | POST、Header、连接重试、Abort 和响应校验 |
| SSE 字节/事件解析 | `packages/chat-sse/src/fetch-event-source/parse.ts` | 跨 chunk 字节缓冲、CR/LF 拆行、UTF-8 解码与空行收口 |
| SSE 协议处理 | `packages/chat-sse/src/chat-sse.ts` | JSON 解析、序号记录、reconnect、完成分类 |
| QAPair 与续传 | `packages/chat-stream/src/actions/request-actions.ts` | 请求实例、回调绑定、checkpoint 和重试 |
| 按业务实体更新 | `packages/chat-stream/src/actions/chat-stream-actions.ts` | 根据 QAPair id 查找并更新轮次 |
| 双答案编排 | `packages/chat-search/src/client/component/chat-answer/answer-generate/components/generate.san` | 分支路由、完成屏障和异常收口 |
| 双答案状态逻辑 | `packages/chat-search/src/client/service/compare.ts` | 状态机、完成策略和幂等判定 |
| Block 渲染队列 | `packages/chat-search/src/client/component/chat-answer/answer-generate/components/ai-entry/index.san` | 入队、合并、消费、ack 和渲染完成 |
| Huabu 组件注册 | `packages/chat-huabu/index.ts` | 将 Markdown、Chart、Image 和业务卡片注册为 `ai-*` 组件 |
| Huabu 组件实现 | `packages/chat-huabu/components/*` | 处理各自数据、交互和 render/typing-finished 契约 |
| Huabu 能力注入 | `packages/chat-huabu/assistant.ts`、`packages/chat-search/src/client/module/assistant.ts` | 将请求、日志、上下文、预览和工作区能力注入通用组件 |
| Hybrid BaseData | `packages/chat-search/src/client/service/base-data.ts` | SWR、共享请求、重试和 ensureToken |
| BaseData 缓存 | `packages/chat-util/util/get-data-form-dom.ts` | 内存缓存、localStorage 和过期判断 |
| 实验一致性 | `packages/chat-search/src/client/service/na-exp-vars.ts` | `nev` 收集、请求拼接和等价判断 |
| Hybrid 初始化屏障 | `packages/chat-search/src/client/component/chat-container/chat.main.san` | BaseData 尝试完成后初始化 ChatStream |
| Store 最小同步 | `packages/chat-search/src/client/store/index.ts` | 只同步 BaseData 派生字段 |
| GuideData 复用 | `packages/chat-search/src/client/service/context-init.ts` | 复用 BaseData，失败回退原接口 |
| Hybrid 构建 | `packages/chat-search/scripts/vite/vite-hybrid-plugin.ts` | ZIP、版本、MD5、资源映射和 manifest |
| Manifest 接口 | `packages/chat-search/src/server/controller/chat/hybrid.controller.ts` | 启动时读取并缓存 manifest |
| Hybrid 监控 | `packages/chat-search/src/client/service/spy.ts` | 模式、资源版本和性能维度 |
| Vite 主配置 | `packages/chat-search/vite.config.ts` | 多入口、多平台 alias、advancedChunks、兼容目标和压缩策略 |
| 本地开发服务 | `packages/chat-search/scripts/vite/vite-search-dev-plugin.ts` | 路由适配、BaseData 注入、Mock/代理和架构检查 |
| 动态 Chunk 预载 | `packages/chat-search/scripts/vite/vite-dynamic-chunk-preload-plugin.ts` | 为高频懒加载资源注入 modulepreload |
| 字体预载 | `packages/chat-search/scripts/vite/vite-cos-icon-preload-plugin.ts` | 构建时解析并预载 PC 图标字体 |
| 极速版产物 | `packages/chat-search/scripts/vite/vite-search-speed-plugin.ts` | 将 CSS/入口 JS 内联到特殊极速 HTML |

## 4. 面试前准备真实指标

文档没有虚构收益数字。正式面试前，应补充自己能确认的真实数据：

| 专题 | 建议准备的指标 |
|---|---|
| 双答案 | 串列问题量、重复完成次数、异常分支比例、实验覆盖量 |
| SSE 恢复 | 重连触发量、重连成功率、弱网失败率、重复/缺失反馈 |
| DOM 队列 | 首 Token、正文完成耗时、P75/P95、渲染异常率、长答案卡顿率 |
| Hybrid 包管理 | 本地包命中率、下载/校验/解压成功率、版本收敛率 |
| Hybrid 动态数据 | BaseData 缓存命中率、接口耗时、ensureToken 成功率、首问成功率 |
| Vite 构建 | 冷/热构建时间、watch 时间、首屏传输体积、Chunk 数、缓存复用字节、FCP/FMP |

没有准确数字时，说明“我会如何通过日志和指标验证”，不要编造百分比。

## 5. 面试中最容易说错的表述

| 不要这样说 | 准确说法 |
|---|---|
| “frameBaseData 是页面 CSR 后再请求的” | 它随 `/search` 的 HTML 文档注入 head；主要业务 DOM 才由客户端 CSR |
| “页面是完整 SSR” | 服务端只给 HTML 壳和启动数据，`#app` 初始为空，不是完整业务 DOM 的 SSR |
| “San attach 完就代表页面全部完成” | 还要区分路由页挂载、首屏数据、回答网络完成和回答 DOM 完成 |
| “所有回答组件和调度逻辑都在 chat-huabu” | Huabu 提供通用组件和 API；Generate/AI Entry 在 chat-search 中负责分支、队列和组件选择，本地组件也会加入注册表 |
| “服务端 component 可以直接当 San 组件名使用” | AI Entry 还会做旧协议归一、camelCase 转换、补 `ai-` 前缀和数据适配 |
| “异步 Huabu 组件加载失败一定会自动释放队列” | 当前同步异常和非渲染包有处理；动态 import Promise reject 的显式 ack 链路仍需补强 |
| “checkpoint 保证不重不漏” | 当前只是 best-effort；严格语义还需要业务提交点和幂等 |
| “服务端一定会返回两个 endTurn” | 前端只根据实际分支集合等待，并为停止、异常和缺包设置收敛路径 |
| “SSE complete 就代表回答完成” | 网络完成后还要等待渲染队列和当前组件 settled |
| “Hybrid 的 Native 更新代码在这个项目里” | 当前仓库实现构建、Hoth 和 Web 运行时；Native 更新属于协同方案 |
| “Hybrid 就是离线包” | Hybrid 还包括 WebView、Bridge、版本治理、降级与监控 |
| “项目现在全量构建就是 90s → 2s” | 这是技术串讲中当时 Wise 特定入口的历史专项数据，不是当前全 monorepo 实测 |
| “Vite 自动把包拆好就行” | 当前使用 advancedChunks、动态 import 和精准 preload 共同治理依赖图 |

## 6. 最后一遍自检

- 能否在 30 秒内讲清项目，而不是罗列功能；
- 能否把文档壳、应用骨架、首屏业务和回答增量渲染四个阶段说清；
- 每个难点能否先说业务矛盾，再说技术名词；
- 能否指出至少一个当前实现的真实边界；
- 能否区分网络状态、业务状态、渲染状态和交互状态；
- 能否说明异常、停止和正常完成如何收敛；
- 能否给出代码位置或接近代码级的伪代码；
- 能否用真实指标闭环，没有数据时是否保持诚实。
