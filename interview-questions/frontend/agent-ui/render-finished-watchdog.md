# 画布组件未抛出渲染完成事件时，如何避免整个流式消费队列永久阻塞？

> 主题：Agent UI / 完成协议 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- 流式状态、时序边界、异常收口与项目真实性。

## 核心答案（能直接讲出口的版本）

这个问题我会先如实说明项目现状，再说如果由我完善会如何设计，不会把还没落地的方案说成现有能力。

当前项目的主要机制是完成事件契约：`ai-entry` 在消费当前 block 时会将 `prevBlockRenderFinished` 设为 `false`，组件完成打字或渲染后必须抛出 `typing-finished` / `render-finished`，再由 `handleBlockRenderFinished` 把标识恢复并继续消费后续队列。对于非渲染包、空 Markdown 包、图片 loading 包等确定不需要等待子组件事件的分支，代码会主动调用 `handleBlockRenderFinished` 放行。

但是，当前 `ai-entry` 中并没有一个通用的“长时间未完成就自动中断”看门狗，`handleBlockRenderFinished` 本身也不会中断 SSE；它的语义正好相反，是标记当前 block 完成并继续消费下一包。因此，如果某个新接入组件本应抛出完成事件却没有抛，现有队列确实存在永久挂起的风险。

如果让我完善，我会在 `ai-entry` 增加一个**按 block 维度的渲染 watchdog**，而不是直接给整条 SSE 设置一个粗暴的固定超时。

每次 `findNextBlockAndRender` 取出新 block 时，为它生成一个本地 `renderToken`，同时启动超时计时器。正常收到 `typing-finished` 或 `render-finished` 后立即清理计时器。超时回调执行前，必须校验 `renderToken` 与当前 `curBlock._index` 仍然匹配，防止上一包的过期 timer 把新 block 错误放行。`clear`、`stop`、组件销毁和切换回答时也要统一清理 timer。

超时阈值不应所有组件共用一个常量。Markdown 等本地同步或打字组件可以设置相对短的阈值；动态加载、图片、文档工作台等重组件需要更长的阈值，最好支持组件上报 progress/heartbeat 来续期。这样可以区分“正常渲染较慢”和“组件已经卡死”，减少误判。

超时后我不会默认立即中断整条 SSE，因为 SSE 数据接收和某个画布组件渲染失败是两个不同层次的问题。我会先记录异常上下文，包括 `component`、`qid`、`seqId`、`sectionId`、block 序号、等待时长、队列长度和端信息；然后尝试调用当前组件的 `stop` / `updateEnd` 停止内部异步任务，将该 block 标记为渲染失败，展示局部兜底 UI，再以幂等方式完成当前 block 并放行后续队列。

只有当该 block 与后续包存在不可分割的强依赖，或者连续多个 block 超时说明整个回答渲染已不可恢复时，才升级为答案级异常，调用上层的停止生成链路中断 SSE。这样可以避免一个局部画布故障直接报废整个 AI 回答。

最后，我会用用例验证三个边界：正常完成时 timer 会被清理；过期 timer 不会放行新 block；单 block 超时后队列能够继续，且完成事件重复到达时不会二次放行。

## 简化记忆

- **项目现状**：依赖 finished 事件契约，没有通用超时 watchdog；`handleBlockRenderFinished` 用于放行，不用于中断 SSE。
- **超时防串**：每个 block 绑定 `renderToken`，timer 触发前校验仍是当前 block。
- **分类阈值**：按组件类型设置超时，长任务通过 heartbeat 续期。
- **分级恢复**：先局部停止、兜底并放行；只有不可恢复时才中断整个回答。
- **可观测性**：上报组件、包序号、等待时长和队列长度，方便定位问题。

## 职责边界表达

如果这套 watchdog 尚未在项目中落地，面试时应表述为“我在理解现有队列契约后提出的完善方案”，不应说成自己已经实现并上线。
