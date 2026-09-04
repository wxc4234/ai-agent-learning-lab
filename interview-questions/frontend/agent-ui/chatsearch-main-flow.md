# 用户点击发送到第一段 AI 内容真正可见，前端经历了哪些关键步骤？

> 主题：Agent UI / 全链路 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-overview.md)

## 考点（面试官在考察什么）

- 流式状态、时序边界、异常收口与项目真实性。

## 核心答案（能直接讲出口的版本）

这条链路在 PC 和 Wise 上的入口不同，但后面会收敛到同一套对话发起流程。

PC 使用前端自己实现的输入框组件，用户发送后会直接进入 `launchConversation`。Wise 的输入框由客户端创建，客户端通过端与 H5 的消息通道发出 `search` 事件。前端收到后，会先处理登录态检查、图片和文件 query 解析等前置逻辑，将相关状态更新到 store，然后同样进入 `launchConversation`。

在 `launchConversation` 里，会组装两类数据：`query` 是发给后端、用于真正语义解析的请求数据；`showQuestion` 是前端用来展示用户提问的视图数据。两者分离，是因为后端消费的结构和用户需要看到的内容并不总是完全一致。

接着会调用 `chatStream.sendPrompt`。ChatStream 先在 store 中创建当前轮的 `QAPair`，所以页面可以立即展示问题气泡和回答等待态，然后才建立 SSE 请求。服务端返回 `generating` 类数据包后，ChatStream 把数据追加到当前回答的 `answerData` 中。

回答数据随后依次经过 `chat-answer` → `answer-generate` → `ai-container` → `ai-entry`。`ai-entry` 会根据服务端下发的组件类型，选择渲染 Markdown、思考过程或其他画布组件。直到第一个可渲染的数据块经过 San 的响应式更新并完成 DOM 渲染，用户才真正看到第一段 AI 内容。

这里一个关键点是：**SSE 的第一个数据包不等于第一段可见内容**。首包可能只是 `baseData` 或 `waiting` 等状态数据，因此“接收到 SSE 首包”和“页面首段正文可见”是两个不同的时间点。

我主要参与的是主应用的业务接入层，包括 PC 新首页和结果页的输入框能力、Wise 收到客户端 `search` 事件后的 H5 兼容处理、`launchConversation` 周边的请求参数处理，以及回答区的业务扩展和多端适配。底层 `chat-sse` 和通用渲染框架是团队的公共基础设施，不是我个人从零搭建的。

## 链路速记

`PC 输入框 / Wise search 事件` → `前置检查与多模态 query 解析` → `launchConversation` → `query + showQuestion` → `chatStream.sendPrompt` → `创建 QAPair` → `建立 SSE` → `generating 数据进入 answerData` → `chat-answer` → `answer-generate` → `ai-container` → `ai-entry` → `San 更新 DOM` → `首段 AI 内容可见`

## 面试表达要点

- 先说 PC 和 Wise 的入口差异，再说两条链路如何收敛。
- 说清 `query` 与 `showQuestion` 的职责分离，不只罗列函数名。
- 区分“SSE 首包到达”与“首段可见内容完成 DOM 更新”。
- 主动说明个人负责边界，避免把团队公共基础设施包装成个人从零实现。
