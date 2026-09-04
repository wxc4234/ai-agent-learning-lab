# Koa BFF 如何流式转发上游 SSE，并处理背压与连接取消？

> 主题：网络 / Node.js BFF | 频率：中

## 考点（面试官在考察什么）

- 服务端流式转发、背压、取消与代理配置。

## 核心答案（能直接讲出口的版本）

核心是 BFF 不解析、不聚合 SSE，而是建立一条可取消且支持背压的流式管道。

响应需要设置 `Content-Type: text/event-stream`、`Cache-Control: no-cache, no-transform` 和 `X-Accel-Buffering: no`，关闭压缩与代理缓冲，不设置 `Content-Length`，并尽早发送响应头，避免 Koa 中间件或网关把流攒成一次性响应。

转发时不能调用 `text()` 读取完整内容，而应将上游 Web `ReadableStream` 转成 Node.js 可读流，再通过 `pipeline` 写入 `ctx.res`。管道会传递背压：浏览器消费变慢时暂停上游读取，等待下游恢复后继续，避免数据无限积压在 BFF 内存中。

同时监听浏览器连接的 `aborted` 和 `close`。下游断开后立即调用 `AbortController.abort()` 取消上游 fetch，销毁相关流并清理监听器，避免 AI 服务继续生成或 BFF 长期占用连接。

错误处理要区分响应头是否已经发送：发送前可以返回正常 HTTP 错误；发送后不能再修改状态码，只能结束 SSE 流并记录日志。线上还需关闭网关缓冲、调整空闲超时，必要时发送 SSE 心跳防止连接被代理切断。

## 链路速记

`关闭缓冲/压缩 → 尽早发响应头 → pipeline 直通并传递背压 → 下游断开时 abort 上游 → 按 headersSent 区分错误处理。`

## 容易说错的地方

- 调用 `response.text()` 会等完整响应，已经破坏流式转发。
- 只在应用层设置 SSE 响应头不够，网关压缩、缓冲和超时也要同步配置。
- 客户端断开后必须取消上游生成，不能只停止向浏览器写数据。
