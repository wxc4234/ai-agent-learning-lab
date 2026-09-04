# 服务端动态下发画布组件时，前端如何完成协议转换、动态渲染和宿主能力隔离？

> 主题：Agent UI / 动态组件 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-overview.md)

## 考点（面试官在考察什么）

- 流式状态、时序边界、异常收口与项目真实性。

## 核心答案（能直接讲出口的版本）

服务端 SSE 包中的 `content.generator` 是 Block 的原始形态，核心字段是 `component` 和 `data`。Generate 层先补充 `qid`、`section` 等上下文，再经过 `AIContainer.pushBlock` 和 `AIEntry.pushBlock`。

`AIEntry` 会统一协议：组件名从 camelCase 转成 kebab-case，并补成 `ai-*`；Markdown 还会把 `data.value` 转为 `content`，补充打字配置、指令解析和引用预处理。引用包会转成数据型 `ai-reference`，更新引用列表并注入 Markdown，而不是独立渲染。归一化后的 Block 进入队列，相邻同类内容可以合并，再按完成信号串行消费。

渲染层由 `@baidu/chat-huabu` 维护组件注册表，将 `ai-markdown`、`ai-chart` 等协议名映射到 San 组件。`AIEntry` 通过 `s-is="block.component"` 动态选择组件，并用 `s-bind="block.data"` 传入数据。未知组件和结束包走非渲染分支；带 `componentConfig` 的组件还可通过异步 loader 加载并缓存。

业务组件不直接依赖主应用的 Store、Channel 或 Native 实现，而是面向 `AssistantAPI`。主应用通过 `AssistantChatAPI` 封装对话上下文、请求、登录、打点、滚动和 Workspace 等能力，再由 `initAssistant` 注入；组件继承 `AssistantComponent` 后还能统一监听 answer-end、stop-answer 等生命周期。

因此新增业务卡片通常只需实现组件、注册协议名并通过 `AssistantAPI` 使用宿主能力，不需要修改 SSE 主链路。

## 链路速记

`generator.component/data` → 补充上下文 → 协议归一化 → Block 队列 → 注册表映射 → `s-is` 动态渲染 → `AssistantAPI` 调用宿主能力。

## 边界与易错点

- 引用包主要更新 Markdown 的引用数据，不一定独立渲染。
- 未知组件必须走可完成的非渲染或降级分支，不能阻塞消费队列。
- 这是业务运行时隔离，不是完全独立的插件系统；当前仍存在 ChatSearch 类型和 `knowledgeStore` 等包级依赖。
