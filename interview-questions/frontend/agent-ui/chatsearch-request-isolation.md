# 上一轮仍在生成时发起新提问或重新回答，如何避免 SSE 串流和旧数据污染？

> 主题：Agent UI / 请求隔离 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-sse-reconnect.md)

## 考点（面试官在考察什么）

- 流式状态、时序边界、异常收口与项目真实性。

## 核心答案（能直接讲出口的版本）

我们项目不是只依赖一个 `qid` 来解决这个问题，而是从请求生命周期、状态定向写入和重答残留包过滤三层处理。

第一层是先收口旧一轮的生命周期。发起新对话前，业务层会广播 `beforeLaunchConversation` 事件，让回答区、打字效果和其他依赖生成态的组件停止旧一轮的 UI 副作用。针对 Agent 或任务模式，`launchConversation` 还会等待上一轮服务端生成取消完成，再发起新请求。在传输层，`chat-stream` 的 `sendPrompt`、重新生成和重试入口都会调用 `stopFetch`，通过当前 `ChatSSE` 实例的 `abort` 中断旧连接。

第二层是回包不按“页面上最新的一轮”盲写，而是定向写回请求发起时绑定的容器。`fetch` 为每个 SSE 实例注册回调时，闭包保留了当次请求所属的本地 `QAPair.id`。数据回来后，`updateAnswer` 会根据这个 id 重新在 `chatStreamData` 中查找目标 `QAPair`，再通过 `aid` 定位具体的 Answer，而不是直接使用可能已经变化的 `currentQAPairIndex`。因此，即使旧请求的回调晚于新一轮触发，它定向的仍然是原来的 QA 对，不会因为“当前轮次”已切换而写入新问题。

这里要区分三个标识：本地 `QAPair.id` 定位哪一轮问答，`aid` 定位该 QA 对下的哪一个答案，服务端数据包里的 `qid` 则标识某一次具体生成，每次生成都可能不同。它们的职责不能混为一个“QAPair ID”。

第三层是对重新回答做额外处理。如果是“再生成一篇”，会在原 `QAPair` 下通过 `addAnswer` 新建一个 `aid`，中断旧流后把新数据写入新 Answer。这个场景虽然复用了 `QAPair.id`，但新旧答案的 `aid` 不同，所以容器层面是分离的。

更需要注意的是“重试并覆盖当前答案”。这条链路会清空指定 Answer，然后复用同一个本地 `QAPair.id + aid` 发起新请求。因此，`QAPair.id + aid` 只能说明数据要写到哪个容器，**它不能区分同一容器的新旧两次请求**。在 `chat-stream` 的 `message` 回调中，当前也没有在写入 `answerData` 前使用服务端 `qid` 或独立的请求版本号进行代次校验。

所以，覆盖重答的主要保障不是“用 `qid` 判断回包属于新请求”，而是在新请求前先 `await cancelLastGeneration()` 收口上一次服务端生成，再由 `stopFetch` 对旧 `ChatSSE` 实例执行 `abort`。`ChatSSE` 会中断当前 `AbortController`、把实例状态设为 `ABORT`，并停止继续消费该实例的缓存消息，然后新请求使用新的 SSE 实例。也就是说，新旧请求的核心隔离边界是 **SSE 请求实例的生命周期**，而不是 `qid`。

重试时设置的 `skipPackages` 只是渲染层的额外兜底：防止已经进入 `chat-answer` 到 `generate.san` 异步更新队列的旧包继续触发画布渲染。它可以利用包中的服务端 `qid` 识别这批已进入渲染链路的遗留数据，但这不等于 `chat-stream` 已经在写 store 前用 `qid` 完成了新旧请求隔离。

如果要在数据层做更强的绝对保障，可以为每次 `fetch` 生成独立的 `requestVersion` 或 `generationToken`，并在 `message` 回调写入前校验它仍是该 `QAPair.id + aid` 的当前活跃版本。这是对现有依赖 `abort` 语义的机制的可增强点，不能冒充成项目已经实现的逻辑。

我实际参与较多的是 `beforeLaunchConversation` 周边的业务状态收口、多端适配和回答区重试时的渲染处理。`chat-stream` 中请求实例管理、`QAPair.id + aid` 的定向更新属于团队公共流式基础设施，我是基于这套机制做业务接入和问题排查，不会把它表述为自己从零设计。

## 简化记忆

- **新问题**：旧生成收口 + `stopFetch` 中断 + 回调闭包保留旧 `QAPair.id`，避免写入新 QA 对。
- **再生成一篇**：复用 `QAPair`、新建 `aid`，新旧答案容器分离。
- **覆盖重试**：复用 `QAPair.id + aid`，不能靠它们区分请求代次；现有主要依赖“服务端取消 + 旧 SSE 实例 `abort`”，`skipPackages` 只处理渲染队列遗留包。

## 容易说错的地方

- `beforeLaunchConversation` 在这里是业务事件广播，不要笼统地说成它自身完成了所有 SSE 中断。
- 不要说“所有回包都根据服务端 `qid` 写入 Answer”。底层状态定位的主键是本地 `QAPair.id`，具体答案由 `aid` 定位。
- 在覆盖重答中，`QAPair.id + aid` 新旧请求相同，它们只能定位容器，不能证明包属于哪一次请求。
- `skipPackages` 是渲染层兜底，不是写入 store 前的请求隔离机制。
- 不要把“重新生成一个新答案”与“清空并覆盖当前答案”混为一条链路。
