# 连续微任务为什么会导致流式页面掉帧，如何主动让出主线程？

> 主题：JavaScript / Event Loop | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- JavaScript 运行机制、异步时序与可运行实现。

## 核心答案（能直接讲出口的版本）

Promise 和 `nextTick` 虽然是异步的，但通常进入微任务队列。浏览器需要先清空本轮微任务，才有机会处理用户输入、执行 `requestAnimationFrame` 和绘制页面。

如果每个 Markdown 包处理完成后又通过 Promise 消费下一包，就可能持续产生新的微任务，让浏览器长期拿不到渲染机会，形成微任务饥饿。`nextTick` 只能等待 San 的数据更新完成，并不等于已经把主线程交还给浏览器。

改造时，SSE 回包只负责按 `seq` 入队，由单一消费者保证顺序。每轮限制处理数量或控制在约 5～8ms 的时间预算内；达到预算后，使用 `scheduler.yield()`、`MessageChannel` 或 `setTimeout(0)` 切到新的任务，让用户输入和渲染获得执行机会，不能用仍属于微任务的 `Promise.resolve()` 代替。

Markdown 数据可以先合并，并在一次 rAF 中提交少量状态更新；但解析等重计算不能全部放进 rAF，否则仍会阻塞当前帧。消费者通过 `processing` 防止重复启动，并保存 cursor，恢复后继续处理下一个 `seq`，因此改变的只是处理时机，不会改变数据顺序。

## 调度速记

`SSE 只入队 → 单消费者按序处理 → 数量/时间切片 → 新任务让出主线程 → 从 cursor 恢复。`

## 容易说错的地方

- Promise 异步不等于浏览器能够绘制，连续微任务仍可能长期占用主线程。
- `nextTick` 保证框架更新时序，不保证完成一次浏览器绘制。
- 重计算不能全部塞进 rAF；rAF 回调超时同样会造成掉帧。
- 让步只改变消费时间，不应改变 `seq` 顺序或启动多个消费者。
