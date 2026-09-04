# San `nextTick` 与 `requestAnimationFrame` 分别保证了什么，为什么测量布局和执行滚动时不能只用 `nextTick`？

> 主题：浏览器 / 渲染时序 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-render-backpressure.md)

## 考点（面试官在考察什么）

- 浏览器机制、生命周期、安全边界与异常场景。

## 核心答案（能直接讲出口的版本）

`nextTick` 和 `requestAnimationFrame` 处理的不是同一层的时序。`nextTick` 解决的是 **San 响应式更新时序**，`requestAnimationFrame` 对齐的是 **浏览器渲染帧时序**。

当我们修改 San 数据时，框架会将多次变更合并到自己的异步更新队列。`nextTick` 表示等待这轮框架更新被 flush，如 `s-if`、`s-for` 对应的节点已经创建、删除或更新，因此这个时候可以安全地拿到新节点和组件 ref。

但 `nextTick` 不代表浏览器已经完成这一轮样式计算、布局和绘制，更不能保证 Markdown 子组件的异步解析、图片加载或字体切换已经结束。所以，在 `nextTick` 刚执行时就直接读取高度并滚动，可能拿到的是中间状态；子组件后续再改变高度，页面就会出现二次跳动。

`requestAnimationFrame` 会把回调安排在浏览器**下一次绘制之前**执行。它不是表示“新一帧已经绘制完成”，而是给我们一个与帧边界对齐的时机。在回调中读取 `getBoundingClientRect`、`offsetHeight` 或 `scrollHeight` 时，浏览器会基于当前已累积的 DOM 和样式变更给出最新几何信息；如果布局还处于 dirty 状态，这个读取也可能触发同步 layout。

因此，比较稳定的时序是：先修改响应式数据，用 `nextTick` 等待 San 完成 DOM patch，再用 `requestAnimationFrame` 对齐浏览器的帧阶段，统一读取几何信息并写入滚动位置。在项目的底对齐中，`ResizeObserver` 负责感知回答内容高度变化，`requestAnimationFrame` 内再计算目标位置并滚动，就是为了避免在多次布局变化中间反复调整滚动条。

不过，`requestAnimationFrame` 也不会自动消除所有布局抖动。如果在同一个回调里反复交错执行“写 DOM → 读布局 → 再写 DOM → 再读布局”，仍然会触发强制同步布局。正确做法是尽量批量读、再批量写，并在高频触发时合并或取消重复的 rAF 任务。

同样，单次 rAF 也不能等待图片、字体和异步画布组件的所有后续高度变化。这些场景还需要依赖 `ResizeObserver`、子组件的 `render-finished` 契约，或具体资源的 load 事件来保证最终稳定。

## 一句话区分

`nextTick` 保证“框架的 DOM 更新已经 flush”，`requestAnimationFrame` 保证“逻辑在浏览器下一次绘制前执行”；两者都不单独保证所有异步子内容已经完全稳定。

## 容易说错的地方

- rAF 回调在下一次 paint 之前执行，不是 paint 完成之后。
- rAF 不保证读取布局没有成本；布局为 dirty 时读取几何信息仍可能强制同步 layout。
- rAF 只是对齐帧时机，不会自动把多次 DOM 读写变成一次。
