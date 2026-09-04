# ChatSearch 为什么从 Rollup 迁移到 Vite/Rolldown，迁移带来了哪些收益？

> 主题：工程化 / Vite | 频率：高 | 关联项目：[ChatSearch AI](../../projects/chatsearch-vite-rolldown.md)

## 考点（面试官在考察什么）

- 构建原理、依赖边界、缓存发布与可验证收益。

## 核心答案（能直接讲出口的版本）

迁移前，ChatSearch 的前端开发和生产构建都使用 Rollup：开发环境通过 `rollup -w` 完成整包编译并监听文件，生产环境也通过 Rollup 构建多套页面产物。迁移后，客户端的开发与生产构建改为 Vite/Rolldown，Node 服务端产物仍保留 Rollup，没有一次性替换整条工具链。

迁移主要解决两个问题。第一，随着页面入口和依赖增加，Rollup 开发时需要先完成整包构建，启动和 Watch 增量编译逐渐变慢。Vite 开发环境先启动服务，项目源码按原生 ESM 按需转换，第三方依赖完成格式转换和预构建后缓存在 `node_modules/.vite`，因此日常修改不需要重新整包构建。第二，原产物中包含较多非首屏或重复依赖，例如 ESL、完整的 search-core、重复版本的 Boxx，以及提前加载的 Hector、AgentUI，需要结合新的拆包能力重新整理依赖边界。

生产构建提速也不能只归因于工具名称变化。Rolldown 使用 Rust 实现模块解析、依赖图构建、链接和 chunk 生成，处理相同依赖图时本身具备性能优势；但项目更大的变化是实际参与构建和进入首屏的内容减少：PC、Wise、Wise-Speed 分入口构建，删除旧页面和 ESL 集成，search-core 只保留 EventBus，统一 Boxx 等重复依赖版本，Hector、AgentUI 延迟加载，缩小浏览器兼容范围，并重新适配 San、HTML 模板和静态资源插件，减少旧多配置、多插件的重复处理。

迁移后的文档数据如下：

- 以 Wise 对话页和分享页两个页面为例，生产构建从约 `90s` 降到 `2s`。
- 开发启动构建从约 `35s` 降到 `1.7s`。
- Watch 增量构建从约 `13s` 降到 `1.7s`。
- 首屏 JS 从约 `6.8MB` 降到 `5.2MB`。
- 首屏 CSS 从约 `1.4MB` 降到 `1.1MB`。
- 项目文档记录缓存利用率提升约 `50%`。
- 项目资料还记录：开启 legacy 插件后，构建时间会从约 `10s` 增长到 `40s`，说明兼容转换链路是重要成本之一。

这些结果不能全部归因于“把 Rollup 换成 Vite”。产物侧还同时完成了按需拆包、只引入 search-core 的 EventBus、移除 ESL 默认集成、统一 Boxx 版本，以及延迟加载 Hector 和 AgentUI。开发侧则补齐了本地 Mock、测试环境代理和直连 RD 环境，不再强依赖远程开发机。

## legacy 插件的成本与取舍

legacy 插件会把一次生产构建扩展成现代版和兼容版两套产物。它会对现代 chunk 再按目标浏览器进行语法降级，生成 SystemJS 格式的 legacy chunks，并补充 SystemJS runtime、必要的 polyfill 和 HTML 分流脚本；随后还要再次执行代码生成和压缩，所以 chunk 越多，额外成本越明显。

最终通常包括现代浏览器使用的 ESM entry/chunks、带 legacy 标识的 SystemJS chunks、`polyfills-legacy`，以及 HTML 中的加载脚本。浏览器通过 `module/nomodule` 分流：现代浏览器执行 `type="module"`，旧浏览器执行 `nomodule` 并通过 SystemJS 加载兼容产物。

结合 ChatSearch 的实际浏览器范围，项目最终没有开启完整 legacy 双产物，而是设置合适的构建 target，并只为确实需要的能力补充 polyfill。`10s → 40s` 是项目当时的实测结果，不是 legacy 插件在所有项目中的固定开销。

## `build.target` 与 polyfill 的边界

`build.target` 主要解决目标浏览器能否解析产物语法，不会自动补齐运行时 API。例如可选链 `obj?.name` 可以根据 target 转换为普通判空表达式；`Promise` 是 JavaScript 运行时对象，需要额外 polyfill；`AbortController` 属于浏览器 Web API，也需要单独兼容，并确认当前 `fetch` 实现是否真正支持 `signal`。无法提供真实网络取消时，只能通过请求版本或标识忽略旧请求的迟到回包，实现逻辑取消。

ChatSearch 当前设置为 `target: 'es2015'`，并在客户端入口统一引入 `@searchfe/polyfills`，集中提供 Promise、AbortController 等兼容实现，而不是依赖 Vite 按使用位置自动注入。`ReadableStream`、`TextDecoder`、`ResizeObserver` 等 API 也需要结合 PC、Wise WebView 的浏览器基线逐项评估。

## 为什么完整 SearchCore 不能只依赖 Tree Shaking

Tree Shaking 的前提不是“业务只使用一个导出”，而是构建工具能够通过静态分析证明其余代码不会执行，删除后也不会改变副作用。项目原入口包含 `import 'esljs'`、`new SearchCore()`、把整个 `chat-stream` 命名空间注册到 AMD 资源系统，以及修改 `window.sc`、注册全局模块等行为，都会产生可观察副作用，构建工具不能安全删除。

如果包入口使用 CommonJS/UMD，或聚合入口先引入所有子模块，依赖关系也不够静态。有效 Tree Shaking 通常要求静态 ESM、正确的 `sideEffects` 声明、细粒度子入口、没有无必要的顶层初始化，并避免插件过早把 ESM 转成 CommonJS。压缩负责缩短代码，Tree Shaking 负责删除可证明不可达的代码，两者不能混为一谈。

`package.json` 的 `sideEffects: false` 是模块级声明，表示未使用导出的模块可以整体删除；`/*#__PURE__*/` 是表达式级提示，表示某次函数调用或 `new` 的返回值无人使用时可以删除，并不代表整个文件无副作用。错误声明可能删除 CSS/Less 导入、Promise/AbortController polyfill、Markdown 图表或动态组件注册，造成样式丢失、旧 WebView 运行失败或组件注册表缺项。CSS、polyfill 和注册模块应进入副作用白名单，PURE 只能标记真正没有外部影响的调用。

ChatSearch 客户端入口中的 polyfill 引入和 Markdown 初始化中的图表注册都属于必须保留的副作用。当前仓库没有统一声明 `sideEffects: false`，依赖治理时需要重点检查这些入口。

因此项目没有继续依赖 bundler 从完整 SearchCore 中裁出 EventBus，而是将 EventBus 作为独立模块，通过 `@baidu/chat-util/event-bus` 直接引入，从源头缩小依赖图。

## pnpm monorepo 中的重复依赖治理

pnpm 的内容寻址解决的是磁盘存储，不保证浏览器 bundle 中只有一份代码。不同版本会在虚拟依赖树中形成不同模块实例；Vite 解析到 `boxx@5.0.15` 和 `boxx@5.0.24` 等不同路径时，仍会把它们视为不同模块。

定位时先通过 `pnpm why <package> -r` 和 lockfile 找到版本及引入链，再用构建可视化报告确认它们是否实际同时进入指定页面和首屏 chunk。lockfile 中存在多版本，不等于单个页面一定加载多份。

治理顺序应是优先升级上游并统一兼容版本范围；带全局状态或 Bridge 注册能力的基础库可以由跨包 `peerDependencies` 约束主应用提供单例。确认 API 兼容后再使用 workspace override；Vite `resolve.dedupe` 可以作为 monorepo 解析兜底，但不能强行合并不兼容的主版本。

项目迁移时把 Boxx 的 `3.0.79、5.0.15、5.0.24` 收敛为 `3.0.79、5.0.24`，主要移除了重复的 5.x；旧播放器依赖的 3.x 仍然存在。因此准确说法是“统一了可兼容的 Boxx 重复版本”，而不是整个 monorepo 只剩一份。多实例除了增加体积，还可能导致事件重复注册和状态不一致，可在 CI 中同时检查重复版本与实际 bundle 变化。

## 表达边界

- 客户端迁移到 Vite/Rolldown，Node 服务端仍使用 Rollup，不能表述为全链路完全替换。
- 构建耗时需要说明统计页面、机器和缓存条件，避免把不同口径的数据直接比较。
- 首屏资源下降是构建迁移与依赖治理、懒加载、拆包共同产生的结果。现有迁移文档只记录“JS 首屏加载体积 `6.8MB → 5.2MB`”，没有写明原始体积、gzip 传输体积或具体页面，不能进一步包装成未经确认的压缩数据。
- “缓存利用率提升约 50%”需要准备具体定义、采集方式和对照周期，不能只报数字。
- 当前资料只有整体 `90s → 2s`，没有完整消融实验，不能精确分配 Rolldown、入口缩减和插件治理各自贡献的秒数。若要严格验证，应在同一机器、同一代码和相同压缩配置下，依次只替换 bundler、移除重插件、缩减入口与依赖，并分别记录模块数、插件耗时、产物体积和冷构建时间。

合理的首屏 JS 统计应固定同一平台和页面，在冷缓存、相同网络与压缩配置下，从导航开始统计到约定首屏完成点之前实际请求的入口、同步依赖、首屏自动动态加载以及 `modulepreload` 下载的 chunk；不包括用户后续操作才加载的低频能力。最好同时记录传输体积和解压后资源体积，但前后必须使用一致口径。

整个 `dist` 同时包含 PC、Wise、分享页、Wise-Speed、Hybrid、服务端代码和互斥场景的异步 chunk，不能代表单个用户的首屏下载量。`dist` 更适合检查总体产物和重复代码；首屏体验需要结合真实请求瀑布、传输字节、FCP/LCP、交互时间和主线程执行成本判断。

## 速记

`Rollup 整包开发慢 + 依赖边界混乱 → 客户端迁移 Vite/Rolldown → 构建提速 + 依赖治理 + 本地开发能力完善；Node 服务端继续保留 Rollup。`
