# PC 和 Wise 如何复用核心逻辑，同时避免多端差异散落在业务代码中？

> 主题：工程化 / 多端复用 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-overview.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

我们的原则是**入口分端、核心收敛、通信适配**。

首先，对于结构和交互差异较大的组件，通过 `.pc.ts`、`.wise.ts` 或对应的 `.san` 文件在入口层隔离。构建时会根据平台优先解析对应文件，因此不需要在一个大组件中遍布 `if (isPc)`。例如输入框和 `chat-init` 就有分端入口。

其次，两端只在用户输入的来源上不同。Wise 由客户端通过 `search` 事件把 query 传给 H5；PC 则由前端输入框自己 `emit search`。两者进入各自入口的 `searchEventHandle` 完成平台参数处理后，最终都收敛到公共的 `launchConversation` 和 `chat-stream`。这样请求状态、`QAPair`、SSE 及回答渲染链路可以共用。

最后，通信差异收口在 channel 适配层。`channel.ts` 主要封装 H5 与客户端的通信，`channel-h5.ts` 主要封装 H5 内部模块之间的通信。例如统一的 `search()` 方法会根据平台在底层选择 `container.search` 或 `conversation.search`，业务层只面向语义化 API，不需要到处关心 EventBus 分区或 Bridge 实现。

这个方案并不是完全消除所有平台判断。像滚动手势、打字速度这类细粒度差异，留在共享控制器中做少量运行时分支更合理；只有结构性差异才拆平台文件，否则会造成两套代码长期分叉。

我实际参与较多的是 PC 新首页和结果页输入能力、Wise `search` 事件接入后的 H5 兼容，以及 `launchConversation` 周边的多端参数收敛。

## 速记

`PC/Wise 平台入口` → `统一 search 语义` → `launchConversation` → `共用 chat-stream 与回答渲染`；结构性差异用平台文件，通信差异用 channel 适配，小行为差异保留少量运行时判断。
