# EventBus 如何根据 `from/to` 决定事件走 H5 本地通信还是 Native 跨端通信？

> 主题：JavaScript / EventBus | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-hybrid.md)

## 考点（面试官在考察什么）

- JavaScript 运行机制、异步时序与可运行实现。

## 核心答案（能直接讲出口的版本）

EventBus 初始化时会创建两类通道：`IntraThreadChannel` 用于 H5 内部通信，底层是本地 `JSEventEmitter`；`InterThreadChannel` 用于 H5 与 Native 通信，底层通过 Boxx DataChannel 和 JSBridge 实现。

路由时，`on/off` 根据事件的 `from` 选择通道，`emit` 根据 `to` 选择通道。当前 EventBus 的运行身份是 `chatjs`、平台环境是 `na`：如果 endpoint 没有配置，或者 endpoint 与当前身份相同，就走本地通道；否则走跨端通道。

例如 Wise 使用的 `container.search` 配置为 `from: 'na'`，没有配置 `to`。因此它的 `on/off` 走 `InterThreadChannel`，通过 `boxx.event.on` 注册 Native 数据通道；同时由于它是一个只有跨端来源、没有去向的事件，对外的 `emit` 会被置为 `null`，形成 Native → H5 的单向通知。

PC 使用的 `conversation.search` 并不是没有配置 `from/to`，而是明确配置为 `from: 'chatjs'`、`to: 'chatjs'`。因为收发端点都与当前 H5 身份一致，所以 `on/off/emit` 全部使用同一个本地 `JSEventEmitter`，不会调用 Boxx。未配置 `from/to` 时默认走本地通道，是 EventBus 的另一条兜底规则。

这套设计把通信方向变成配置，而不是散落在业务代码里的平台判断。业务侧只使用 `on/emit`，不需要关心底层究竟是本地事件还是 JSBridge。

## 速记

`on/off 看 from，emit 看 to；endpoint 与当前身份相同或为空走 IntraThread，否则走 InterThread。`

## 容易说错的地方

- `from/to` 表示事件端点，通道选择还需要结合当前 EventBus 的运行身份判断，不能简单记成 `from` 是前端、`to` 是客户端。
- PC 的 `conversation.search` 显式配置了 `from/to: chatjs`，不是依靠“未配置默认本地”。
- Wise 的 `container.search` 不只是业务上约定单向；当前生成结果中它的 `emit` 确实为 `null`。
