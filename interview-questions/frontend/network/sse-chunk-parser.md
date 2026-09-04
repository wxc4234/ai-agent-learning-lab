# ReadableStream 返回的 chunk 为什么不能直接当成完整 SSE 消息，项目如何正确拆包？

> 主题：网络 / 流解析 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-sse-vs-websocket.md)

## 考点（面试官在考察什么）

- 协议边界、传输语义、异常处理与工程取舍。

## 核心答案（能直接讲出口的版本）

`reader.read()` 返回的是底层字节 chunk，它的边界受 TCP、代理、浏览器缓冲等因素影响，与服务端发送次数和 SSE 消息边界没有对应关系。所以一个 chunk 可能只有半行，也可能包含多行甚至多个 SSE event。

项目分两层处理。第一层 `getLines` 先在字节层面跨 chunk 拼行：没找到 `\r` 或 `\n` 时，保留未完整字节并与下一个 chunk 合并；如果 `\r\n` 恰好被拆开，会记录状态并在下一个 chunk 忽略配对的 `\n`。因为它会等到完整行形成后才交给 `TextDecoder` 解码，UTF-8 多字节字符即使被切在两个 chunk 中也不会被分别误解码。

第二层 `getMessages` 再按 SSE 协议组装 event：解析 `data`、`event`、`id` 和 `retry` 等字段，多个 `data:` 行用换行符拼接，遇到空行才认为当前一条 SSE event 结束并抛给上层。

因此，解析边界是由 SSE 协议的“行和空行”决定的，而不是由 `read()` 返回的 chunk 决定。

## 速记

`chunk ≠ line ≠ SSE event`：`getLines` 跨 chunk 拼完整行，`getMessages` 等空行再完成一条 event。
