# 首屏 JS 体积下降，但线上 LCP 没有改善，如何分层定位？

> 主题：性能 / Core Web Vitals | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-vite-rolldown.md)

## 考点（面试官在考察什么）

- 指标口径、瓶颈定位、优化方案与验证闭环。

## 核心答案（能直接讲出口的版本）

首屏 JS 变小但 LCP 不变，首先说明减少的代码可能不在 LCP 关键路径上。应先确认 LCP 元素是什么，以及被拆走的 chunk 原来是否在该元素出现之前加载和执行，再从四层定位：

- **网络下载**：通过 Network 和 Resource Timing 比较 HTML TTFB、关键 JS 的开始与结束时间、缓存命中、请求优先级和瀑布。如果体积下降但下载完成时间没提前，可能是接口延迟、CDN、连接建立或请求数量抵消了收益。
- **JavaScript 执行**：在 Performance 中检查 JS 下载完成到 LCP 之间的 `Evaluate Script`、初始化逻辑和 Long Task。资源已下载但主线程持续繁忙，瓶颈在解析执行而非下载。
- **数据请求**：把 BaseData 起止、搜索事件、打招呼接口和 SSE 首包放到同一时间线。如果页面必须等数据返回才创建 LCP 元素，可以用本地 Mock 立即返回做对照，验证接口 TTFB 是否是关键路径。
- **浏览器渲染**：比较数据到达、San 更新完成和 LCP 的时间差，进一步检查 `Recalculate Style`、Layout、Paint，以及字体、图片、复杂 Markdown 和大 DOM 是否阻塞上屏。

项目已有 `chatPagePerf` 记录页面 TTFB、资源耗时、BaseData 起止、打招呼数据和 `contentPaintTime`。可以进一步补充浏览器 LCP 时间和元素信息，形成完整时间线：

`页面响应 → JS 下载 → JS 执行 → 数据返回 → DOM 渲染 → LCP`

哪一段没有随着包体积下降而缩短，主要瓶颈就在哪一层。最终需要按 PC、Wise、Hybrid、设备性能和网络类型观察线上 p75，不能只根据本地一次 Performance 录制作结论。

## 排查边界

- 包体积下降不等于关键路径缩短，被拆走的低频代码可能原本就不影响 LCP。
- 下载时间、执行时间和数据等待时间必须分开观测，不能只看单一总耗时。
- 本地 Mock 是定位数据依赖的对照实验，不代表线上最终性能结论。
