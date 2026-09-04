# 多端构建为什么使用 `advancedChunks`，如何平衡首屏体积、缓存和请求数量？

> 主题：工程化 / Vite 拆包 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-vite-rolldown.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

拆包依据不是文件大小，而是**加载时机和变更频率**。

San、Store、Boxx、`chat-stream` 等首屏必需且相对稳定的依赖进入 `vendors`，与高频变化的业务代码分离，减少业务发布造成的缓存失效。Markdown、Mermaid、Markmap、ECharts 等重型低频能力拆成异步 chunk；多种能力共享的 KaTeX、D3 等进入 `diagram-vendors`，各自专属依赖再独立拆分，避免重复打包。

拆分时主要看加载时机、体积、变更频率和依赖关系。同一个 chunk 内的代码应尽量满足“同时被使用、同时发生变化”：首屏必用、体积适中且更新频率接近的基础依赖可以聚合；ECharts、Lottie、Cosmic 组件等大型或特定场景能力单独拆分；Mermaid、Markmap 等总是一起加载的依赖簇分别形成 `mermaid-vendors`、`markmap-vendors`，避免过度细拆造成瀑布。

项目也不会无限细拆：只有被多个入口复用且超过 10KB 的代码才进入 `common`；首屏高频使用的 highlight 插件通过 `modulepreload` 提前加载，减少动态加载瀑布。

普通动态 `import()` 要等代码执行到对应位置时，浏览器才发现并下载 chunk；如果该 chunk 还有公共依赖，可能继续形成请求瀑布。`modulepreload` 在 HTML 解析阶段提前下载模块并放入 module map，真正执行仍等待 `import()` 触发，因此改变的是下载时机，不是懒执行语义。

项目没有预加载所有 Markdown、图表和业务组件，而是通过构建插件从产物中定位带 hash 的高频动态 chunk，再注入 HTML。目前明确预加载的是代码高亮插件 `marklang-rehype-highlight`。选择标准是首屏出现概率高、触发时间早、体积可控，并且线上存在明显加载瀑布；Wise-Speed 使用单 bundle，不再额外注入。

不能对所有异步 chunk 使用 `modulepreload`：低频组件提前加载会浪费流量和内存，与首屏入口、CSS、BaseData 和 SSE 抢占网络，并增加解析、编译压力，从而削弱代码拆分的收益。

不同产物采用不同策略：PC 和 Wise 分别构建并隔离资源；Hybrid 复用 Wise 的 chunk 图，把搜索入口的同步 JS、CSS 放进离线包，重能力仍按需加载；Wise-Speed 更关注请求数量，因此关闭 `advancedChunks`，直接生成单 bundle。

验证时以 search 入口为起点，递归统计同步 JS、CSS 和 preload 的压缩体积、请求数与依赖深度，同时检查重能力是否仍留在异步 chunk、公共依赖是否只存在一份、业务更新后 `vendors` hash 是否稳定。线上再结合首屏耗时和 Resource Timing 观察加载长尾。

实际产物中已经形成 `chat-util/markdown/diagram`、`diagram-vendors`、`mermaid-vendors`、`markmap-vendors` 等独立 chunk，说明模块归属和缓存边界按设计落地。整轮首屏优化后，所有端场景聚合的“页面开始加载到页面展示”平均耗时下降约 700ms；这是拆包与其他首屏优化的共同结果，不能全部归因于 `advancedChunks`。

## 手动拆包的执行顺序风险

如果模块 B 通过静态 `import` 依赖模块 A，ESM 会保证依赖先执行。真正危险的是未写入依赖图的隐式顺序，例如使用方直接读取 `window.xxx`、依赖某个组件已注册，或假设 EventBus 监听已经建立。拆分后可能出现全局能力尚未注册、事件提前发出、公共 chunk 中副作用提前执行或重复初始化，并且常在冷缓存、弱网和独立入口下偶现。

排查时应检查产物依赖图和顶层全局注册，通过关闭缓存、限制网络、阻塞注册 chunk 验证时序，并分别从 PC、Wise、分享页、Hybrid 等入口直接访问。避免方式是把隐式顺序改成显式依赖：注册模块导出幂等 `init()` 或初始化 Promise，使用方显式 import 并等待；真实副作用由入口明确导入并正确声明 `sideEffects`。`modulepreload` 只能提前下载，不能保证模块先执行。

项目中各端 bootstrap 显式导入 `base.ts`；`getEcharts` 返回动态 import Promise，Markdown 图表显式传入 `marklang` 初始化，具体插件再通过异步 loader 加载，避免依赖模块曾经碰巧位于同一个 vendors 中的执行顺序。

## 策略速记

`首屏必需且稳定 → 聚合缓存；重型低频 → 按需加载；多能力共享 → 单独复用；过小模块 → 不拆；特殊入口 → 按目标单独配置。`

## 容易说错的地方

- chunk 不是越多越好，还要控制请求数量和动态加载瀑布。
- 不能只看整个 dist 体积，应分析首屏入口的同步依赖链。
- 700ms 是整轮首屏优化的聚合结果，不能全部归因于拆包。
- 大型 `vendors` 中任一依赖升级都会使整体 hash 失效；过度细拆又会增加请求调度、响应头和模块解析成本，需要结合缓存命中与实际瀑布验证。
