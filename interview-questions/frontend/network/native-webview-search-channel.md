# Wise 输入框在 Native、ChatSearch 在 WebView，两端的 `search` 事件是如何通信并进入对话链路的？

> 主题：网络 / Hybrid 通信 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-hybrid.md)

## 考点（面试官在考察什么）

- 协议边界、传输语义、异常处理与工程取舍。

## 核心答案（能直接讲出口的版本）

这套通信可以分成三层：底层是 Boxx DataChannel，中间是 EventBus 的通道路由，上层是业务 channel 适配。

底层通过 `@baidu/boxx` 屏蔽 Native JSBridge 的系统差异。`boxx.event.on` 对应 DataChannel 的 `/datachannel/register`，`boxx.event.emit` 对应 `/datachannel/sendbroadcast`。两端使用 action 标识信道，消息体再携带具体的事件 `name` 和 `params`。

中间层 EventBus 负责根据配置选通道。页面初始化时，当前 H5 的身份是 `chatjs`，运行环境是 `na`。EventBus 遍历事件配置，分别使用 `to` 生成 `emit`、使用 `from` 生成 `on/off`；如果 endpoint 与当前身份相同或没有配置，就走本地 `IntraThreadChannel`，否则走基于 Boxx 的 `InterThreadChannel`。

以 Wise 的 `container.search` 为例，它配置了 `from: 'na'`，没有配置 `to`，因此监听侧选择跨线程通道，并且对 H5 暴露 `on/off`、将 `emit` 置为 `null`。跨线程通道会生成类似 `com.baidu.searchbox.chatsearch.container.sendCommand.<channelId>` 的 action，再通过 `boxx.event.on` 注册 `window.chat_search_na_event`。Native 发送 `search` 后，Boxx 先按 action 将数据送入这个回调；回调解析出 `name: search` 和 `params`，再转发给对应的本地 `JSEventEmitter`，最终触发 `container.search.on`。

PC 的 `conversation.search` 则配置为 `from/to` 都是 `chatjs`，所以收发两端都走 `IntraThreadChannel`，直接在 H5 内部通过本地 emitter 通信，不会调用 JSBridge。

最上层的 `channel.ts` 再屏蔽业务端差异：同一个 `search()`，Wise 返回 `container.search`，PC、DeepSeek 页和移动浏览器返回 `conversation.search`。`chat-init.ts` 只需要统一执行 `search().on(...)`，收到事件后进入 `searchEventHandle` 解析参数、更新 Store，再调用 `launchConversation`。

这套设计的价值是把**协议、通道路由和业务语义解耦**。业务层不依赖 Boxx 和具体 action；新增事件时主要补充配置和语义化接口，多端可以共享后续对话链路，也更方便独立测试 H5 内部逻辑。

## 速记

`from/to + 当前身份` 决定 `IntraThreadChannel / InterThreadChannel`；`action` 定位 DataChannel 信道，`name` 匹配业务事件；上层 `channel.search()` 再把 Wise 和 PC 收敛成统一语义。

## 容易说错的地方

- `container / conversation / component` 是事件域，不是底层通道类型。
- `container.search` 没有 `to`，当前实现会把跨端 `emit` 置为 `null`，不是仅靠团队约定禁止调用。
- Boxx 层按 action 路由；项目统一回调收到数据后，当前代码只按 `name` 匹配 emitter，并未再次校验 action。
