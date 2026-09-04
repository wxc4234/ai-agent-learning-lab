# `ResizeObserver` 回调再次改变元素尺寸时，浏览器如何处理反馈循环，项目中如何避免循环触发？

> 主题：浏览器 / ResizeObserver | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- 浏览器机制、生命周期、安全边界与异常场景。

## 核心答案（能直接讲出口的版本）

如果 `ResizeObserver` 回调在执行过程中又修改了被观察元素或其布局链路上的尺寸，可能在同一次渲染更新中再次产生 resize observation。浏览器不是简单地无限同步调用回调，而是按被观察节点在 DOM 树中的深度逐轮处理活跃通知，并将当前轮无法安全继续派发的通知延后到后续渲染周期。

如果当前轮仍存在未能派发的 observation，浏览器会向 `window` 报告 `ErrorEvent`，常见信息是 `ResizeObserver loop completed with undelivered notifications.`。这个机制的作用是防止页面在单帧内无限循环导致卡死，不代表业务循环已被修复。如果代码在每一帧都继续改变尺寸，反馈循环仍然可以跨帧持续，并带来抖动和性能问题。

我们项目的自动底对齐主要从以下几点降低这个风险。

第一，观察目标和写入目标分离。`ResizeObserver` 观察的是回答内容区 `scrollContent`，回调最终修改的是外层滚动容器的 `scrollTop`。普通滚动只改变可视位置，不会直接改变 `scrollContent` 的宽高，因此不会形成“观察尺寸 → 回调再改同一尺寸”的直接闭环。

第二，将布局读取和滚动写入分开。observer 回调中先通过 `getBoundingClientRect`、`offsetHeight` 等信息计算目标位置，实际 `scrollTo` 放到 `requestAnimationFrame` 中执行。这样可以避免在 `ResizeObserver` 的当前派发轮次里紧接着执行滚动写入，也便于把多次 DOM 变化收口到浏览器帧边界处理。

第三，在执行滚动前有明确的早退和停止条件。如果内容底部已经在容器可视底部之内，直接 `return`，不做无效滚动；当正文已达到一屏的理想停止位置，或检测到用户主动向上查看时，调用 `stopBottomAlign` 对 `scrollContent` 执行 `unobserve`，从源头停止后续 observer 回调。

第四，程序主动向回修正滚动位置时，会在执行 `scrollTo` 前同步更新 `maxScrollY`。这样后续 scroll 监听不会把程序自己的位置修正误判为用户向上滚动，避免“自动对齐触发停止对齐”的状态反馈。

当前线上实现已经保存单一 `rafId` 来合并高频尺寸变化产生的滚动任务：observer 回调只更新最新目标位置，存在尚未执行的 rAF 时不再重复调度，从而保证一帧最多执行一次滚动。`stopBottomAlign` 和组件销毁时也会清理 `rafId`，避免停止跟随后仍执行过期的滚动任务。

## 简化记忆

- **浏览器保护**：按 DOM 深度处理 active observation，无法派发的通知延后，并向 window 报 error。
- **保护边界**：只防止单帧死循环，不会自动修复跨帧尺寸反馈。
- **观察与写入分离**：观察 `scrollContent` 尺寸，修改外层容器 `scrollTop`。
- **读写分离**：observer 计算几何信息，rAF 写入滚动位置。
- **停止条件**：已在可视区就早退，达到一屏或用户上滑就 `unobserve`。
- **调度合并**：使用单一 `rafId` 合并高频滚动任务，一帧最多执行一次滚动；停止跟随和销毁时同步清理。
