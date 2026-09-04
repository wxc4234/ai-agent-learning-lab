# SSE 回包频率很高时，如何控制流式数据的消费与渲染节奏？

> 主题：Agent UI / 流式渲染 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- 流式状态、时序边界、异常收口与项目真实性。

## 核心答案（能直接讲出口的版本）

我们项目不是每收到一个 SSE 包就立即创建组件或直接操作 DOM，而是通过**队列串行消费、连续组件复用和可合并包批处理**来控制渲染压力。

第一层是渲染背压。每个 block 到达 `ai-entry` 后会先进入 `queue`，而不是立即渲染。`prevBlockRenderFinished` 表示上一个 block 是否已经完成：只有它为 `true` 时才会进入 `findNextBlockAndRender`。开始消费当前 block 时会先把标识置为 `false`；等子组件抛出 `typing-finished` 或 `render-finished` 后，`handleBlockRenderFinished` 再通过 `nextTick` 把标识恢复，继续消费队列中的下一包。

这实际上是一种前端渲染背压：后端数据可以继续进入缓存队列，但渲染端始终等待上一个 block 完成后再处理下一个，避免多个异步组件同时更新 DOM，也保证不同画布组件的展示顺序。

第二层是组件实例复用。对于相邻的同类型 block，只要没有 `standalone`、组件完成标识或其他必须拆分的条件，`ai-entry` 就会复用 `accBlocks` 中已经创建的组件实例。例如连续 Markdown 包会通过组件 ref 调用 `appendContent`，在原有 Markdown 组件中追加内容；其他组件会根据能力调用 `updateData` 或它们自己的增量方法，而不是每一包都新建一棵组件树。

第三层是包合并。在 Wise 端，`findNextBlockAndRender` 会对当前已经积压在队列中的 block 执行 `mergeBlocks`。只有连续、组件名相同、`sectionId` 相同并且配置了 merge 策略的包才会合并。例如 `ai-markdown` 会拼接 `content`，`ai-reasoning-content` 会拼接 `value`，合并后再一次交给组件渲染，从而减少响应式更新和 Markdown 解析次数。PC 端当前不走这层队列合并，以保持产品需要的打字节奏。

最后才是打字机本身的节奏控制。`300ms` 不是全端、全场景的固定周期：它是 Wise 端在特定 `typingHideMask === 'all'` 的 CSR 打字兜底配置中，用来降低取包和渲染频率。当需要逐字效果时，Wise 兜底速度是 `30ms`，PC 兜底是 `10ms`；如果服务端已下发 typing 配置，还会优先使用服务端配置。因此面试时不应笼统地说“整个流式渲染每 300ms 处理一次”。

这套方案的效果是：用队列保证顺序并建立背压，用实例复用降低组件创建和销毁成本，用批量合并减少更新和解析次数，再用打字节奏平衡内容可读性与性能。

我实际参与较多的是回答区业务组件的接入、多端渲染适配，以及定位组件未正确抛出完成事件时的流式卡住问题。`ai-entry` 的通用队列、合并策略和打字调度属于团队公共渲染基础设施，不是我个人从零搭建的。

## 简化记忆

- **队列背压**：入包先进 `queue`，上一包 finished 后再消费下一包。
- **实例复用**：连续同类组件通过 `appendContent` / `updateData` 增量更新。
- **Wise 批合并**：当前队列内连续、同组件、同 section 的可合并 block 一次处理。
- **打字节奏**：`300ms` 仅是 Wise 特定模式的兜底值，不是全局固定周期。

## 方案边界

这套串行消费机制对子组件的完成事件有强依赖。如果某个渲染分支既没有抛出 `typing-finished` / `render-finished`，也没有主动调用 `handleBlockRenderFinished`，`prevBlockRenderFinished` 就无法恢复，后续队列会被挂住。因此新增画布组件时，完成事件是必须验证的接入契约。
