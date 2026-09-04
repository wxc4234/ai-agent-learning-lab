# 自定义 Vite 插件导致启动和构建变慢，如何定位与优化？

> 主题：工程化 / Vite 插件 | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-vite-rolldown.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

先把耗时拆成 Vite 进程启动、首个页面可访问和生产构建三段，避免把远程 HTML 或代理请求慢误认为 Vite 启动慢。

定位时先按组临时关闭 San、Mock、`search-dev`、画布校验等插件进行二分。Mock 和代理通常只影响开发，Hybrid、Terser 后处理只影响生产；开发和生产同时变慢时，优先检查两边都会执行的 San 转换或通用 `transform`。

缩小范围后，为各 hook 统计调用次数、总耗时、最大耗时和最慢文件：

- `config/buildStart`：是否阻塞启动；
- `resolveId/load/transform`：是否对大量模块重复执行；
- `configureServer`：是否拦截范围过大或等待远程 HTML；
- `generateBundle/writeBundle`：是否重复扫描产物、压缩或同步读写。

再结合 `vite --debug plugin-transform` 和 `vite --profile` 判断主要成本来自 AST、文件 IO、网络还是压缩。项目画布组件校验已支持 `DEBUG_HUABU_CHECK=1`，可输出每次 `transform` 的耗时，并在 `buildEnd` 汇总次数、总耗时和最大耗时。

常见优化包括：按文件类型和目录过滤；缓存正则、配置、目录结果和解析结果；HMR 只处理变更文件；将可并行的文件处理和压缩改为批量或受控并行；使用 `apply: 'serve'/'build'` 隔离环境；为 HTML 代理增加路由过滤、缓存和超时；插件禁用时避免顶层加载重依赖。

Vite 8/Rolldown 的 hook filter 可以把 `id`、`moduleType` 或源码过滤规则交给 Rolldown，在 Rust 层排除无关模块，避免每个模块都跨到 JavaScript 执行一次空 hook。它降低的是无关模块进入 hook 的固定开销，命中的 San 编译或 AST 分析成本仍需单独优化。

兼容插件应在 handler 内保留相同判断，使不支持 hook filter 的旧版 Vite/Rollup 仍保持正确，只是没有性能收益。模块 ID 可能带 `.san?type=style` 等查询参数，不能只用 `.san$`；filter 与回调内规则应共用条件，避免漂移。项目当前 Vite 8.1.5 可以使用该能力，画布组件校验插件可先在 Rolldown 层限制到 `chat-huabu/components`，再在回调中精确判断文件。

验证时在相同机器和参数下比较冷启动、首次页面加载、单文件 HMR 和生产构建的多次中位数，同时确认功能和产物语义没有变化。

## 前端基础与框架
