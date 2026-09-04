# 为什么项目选择基于 `fetch + ReadableStream` 的 SSE，而不是原生 `EventSource` 或 WebSocket？

> 主题：网络 / SSE | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-sse-vs-websocket.md)

## 考点（面试官在考察什么）

- 协议边界、传输语义、异常处理与工程取舍。

## 核心答案（能直接讲出口的版本）

框架搭建阶段，我们结合业务通信模型对 SSE 和 WebSocket 做过比较。我们的场景是用户发起一次复杂请求，服务端持续单向返回 AI 结果，客户端不需要在生成过程中与服务端频繁双向通信，因此选择了基于 `fetch + ReadableStream` 的 SSE。

没有用原生 `EventSource`，主要有三个原因：

1. 我们需要使用 `POST` 并携带完整 JSON body，而 EventSource 只支持基于 URL 的 GET 连接。
2. 请求需要携带反作弊和业务 headers，EventSource 不支持任意自定义请求头。
3. `fetch` 可以用 `AbortController` 统一中断请求，并且能在读流前通过完整 `Response` 检查 HTTP status 和响应头，错误处理比 EventSource 的 `onerror` 更细。

实现上，项目使用 `response.body.getReader()` 持续读取字节流，再按标准 SSE 格式解析 `event`、`data`、`id` 和 `retry`。所以我们只是用 fetch 替代 EventSource 做传输，协议仍然是 SSE。

也没有选 WebSocket，因为当前是一次请求对应一条服务端单向生成流，不需要全双工长连接。如果改用 WebSocket，还要额外处理连接复用、消息关联、心跳和断线恢复等问题，与当前业务模型不匹配。同时，业务请求需要在请求头和请求体中携带鉴权及业务参数，基于 `fetch` 发起 SSE 请求更容易沿用现有 HTTP 请求体系。

## 速记

`EventSource` 受限于 GET、无 body、无自定义 headers；WebSocket 能力过重；`fetch + ReadableStream` 最匹配“复杂 POST 请求 + 服务端单向流式输出”。
