# 长会话内存持续增长时，如何定位泄漏并统一治理资源生命周期？

> 主题：性能 / 内存 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-overview.md)

## 考点（面试官在考察什么）

- 指标口径、瓶颈定位、优化方案与验证闭环。

## 核心答案（能直接讲出口的版本）

我会先固定操作路径，例如连续进入会话、发送消息、退出页面十次，并比较每轮主动 GC 后的内存。如果增长与历史回答一致，清空会话后能释放或最终趋于稳定，通常属于正常数据增长；如果旧组件、Detached DOM 和回调数量随操作次数线性增加，离开页面后也不释放，则更像泄漏。

定位时使用 Heap Snapshot 对比前后对象和引用链，重点检查 SSE 是否 abort、EventBus 是否取消订阅、Observer 是否 disconnect、定时器是否 clear，以及动态组件是否释放回调和 DOM 引用。修复后重复同一路径，确认 GC 后的内存和资源数量回到稳定范围。

工程上可以设计统一的 `LifecycleScope`：所有资源都对外转换成 `dispose()`，例如 SSE 封装 abort、EventBus 返回 off、Observer 返回 disconnect、定时器返回 clear、动态组件返回销毁函数。San 组件销毁或路由离开时，只需统一执行 `scope.dispose()`。

`LifecycleScope` 需要满足四个约束：清理幂等且单个异常不影响其他 disposer；scope 销毁后才注册的异步 disposer 必须立即执行；page、answer、component 按层级建立子 scope 并级联清理；已进入事件队列的迟到回调还要通过 active 状态或 `generationId` 阻止继续更新 Store。

开发环境还可以统计每个 scope 注册的 SSE、listener、Observer 和 timer 数量，页面退出后未清空就报警，将泄漏提前暴露在测试阶段。

## 治理速记

`固定路径复现 → Heap Snapshot 查引用链 → 资源统一为 disposer → 分层 scope 级联清理 → generationId 屏蔽迟到回调。`

## 容易说错的地方

- 内存增长不一定是泄漏，需要观察 GC 后是否回落以及是否与业务数据规模一致。
- 调用资源清理函数后，已经排队的旧回调仍可能执行，需要额外的有效性守卫。
- disposer 必须幂等，且某一个清理失败不能阻断其他资源释放。
