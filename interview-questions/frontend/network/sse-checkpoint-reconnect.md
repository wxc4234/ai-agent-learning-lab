# SSE 连接中断后如何处理，断点续传如何避免数据重复或丢失？

> 主题：网络 / SSE | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-sse-reconnect.md)

## 考点（面试官在考察什么）

- 协议边界、传输语义、异常处理与工程取舍。

## 核心答案（能直接讲出口的版本）

先需要区分两种中断：**普通网络异常**和**服务端通过 SSE 协议下发的 `reconnect` 事件**。

在我们项目中，普通网络断开后不会盲目自动重连。`ChatSSE` 的 `onerror` 会根据错误发生在 waiting 之前还是 generating 阶段，将它转成 `BEFORE_WAITING_ERROR` 或 `GENERATING_ERROR`，中断当前 SSE；`chat-stream` 随后把对应 `QAPair` 置为 ERROR、Answer 置为 ABORT，交给业务层展示错误或提供手动重试。

项目中的自动断点续传是由**服务端主动下发 `reconnect` 事件**触发的，它不等于浏览器检测到断网后自行重连。每个正常 SSE 数据包都带有 `seq_id`，`ChatSSE` 会持续记录已接收包中的最大 `maxSeqId`。

当收到 `reconnect` 事件时，`ChatSSE` 会以 `RECONNECT` 类型中断当前请求，并把该次生成的 `qid` 以及当前 `maxSeqId` 传给 `chat-stream`。`chat-stream` 清理旧 SSE 实例后，通过 `checkpointFetch` 发起续传请求，关键参数是：

```text
checkpoint.qid = 当前生成 qid
checkpoint.seq_id = maxSeqId + 1
```

传入 `maxSeqId + 1` 表示让服务端从前端尚未消费的下一包开始续传，避免再次下发最后一个已接收包。续传请求不会新建 QA 轮次：`checkpointFetch` 会通过本地 `QAPair.id` 找到原来的 `QAPair`，再对它调用 `fetch`，因此新包会继续追加到原回答容器。

续传时还有一个容易忽略的细节：新 `ChatSSE` 实例的 `maxSeqId` 会初始化为 `checkpoint.seq_id - 1`，也就是上一次已消费的最大序号。这样即使新连接还没收到数据就再次收到 `reconnect`，也不会因为 `maxSeqId` 重置成 `-1` 而从头请求。

系统也对协议型重连设置了上限：普通对话默认最多续传 2 次，深度决策等任务模式最多 12 次。超过上限后会把当前 QA 状态转为 ERROR/ABORT，避免无限重连。

严格来说，“不重不丢”是前后端协议共同保证的：前端用 `maxSeqId + 1` 表达续传起点，服务端需要保留该 `qid` 对应的生成上下文并按 `seq_id` 有序续传。当前前端代码没有对每一包执行本地去重，也没有检查 `seq_id` 是否存在缺口；因此面试时不应将它表述成前端单方就能提供的绝对保证。

我主要是基于这套公共流式能力做主应用的业务接入、状态展示和异常场景适配。`seq_id` 记录、`ChatSSE` 中断以及 `checkpointFetch` 属于团队公共基础设施，不是我个人从零设计的。

## 简化记忆

- **网络错误**：不自动断点续传，当前回答进入 ERROR/ABORT。
- **协议型 `reconnect`**：服务端事件 → 中断旧流 → 携带 `qid + maxSeqId + 1` 调用 `checkpointFetch`。
- **容器复用**：续传原 `QAPair`，不新建问题或答案容器。
- **保障边界**：不重不丢依赖前端 checkpoint 与服务端有序续传协议，不是前端本地去重或缺口检测。
