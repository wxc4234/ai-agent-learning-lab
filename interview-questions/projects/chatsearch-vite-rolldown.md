# ChatSearch：Vite/Rolldown 构建优化

> **难点一句话：**这项工作不是把命令从 Rollup 改成 Vite，而是在 San.js 大型多页面项目中同时重做开发服务器、SFC 编译、分包、兼容目标、按需加载和多平台产物，并避免“构建变快了、线上首屏却因为碎片化变慢”。

## 1. 这个点值不值得在面试中讲

值得，但它更偏工程化和性能体系：

| 面试方向 | 推荐程度 | 原因 |
|---|---|---|
| 前端工程化、基础架构 | 很适合主讲 | 有迁移背景、插件、分包和兼容决策 |
| 高级前端、性能优化 | 适合主讲或第二难点 | 能连接构建速度、资源体积和首屏瀑布 |
| 业务架构、状态管理 | 适合作为补充 | 技术深度足够，但业务状态复杂度不如双答案 |
| 如果本人未参与迁移 | 只做项目背景 | 可以讲理解，不能声称自己主导或直接拥有收益 |

最有价值的表达不是“Vite 比 Rollup 快”，而是：

> 我们先把研发效率、首屏下载和兼容成本拆成三个目标，再分别通过工具链迁移、依赖图治理和运行时加载策略解决；每项优化都有边界，不能只看构建时间。

## 2. 先讲清楚：什么是 Vite，它和 Rollup、Rolldown 是什么关系

面试时不能直接说“我们用 Vite 替换了 Rollup，所以构建变快了”。这句话把不同层级的工具混在了一起。

### 2.1 什么是 Vite

Vite 是一套面向现代 Web 项目的**前端开发与构建工具**。它不只负责把代码打成 bundle，而是提供了一整套工程能力：

- 开发服务器：启动本地页面、处理路由、代理和静态资源；
- 模块按需转换：浏览器请求哪个业务模块，开发阶段就转换并返回哪个模块；
- 依赖预构建：把 CommonJS/UMD 依赖转换为 ESM，并把内部模块很多的依赖合并，减少开发阶段的请求数；
- HMR：修改一个模块后只更新受影响模块，不重新构建和刷新整个应用；
- 生产构建：分析依赖图，执行 Tree Shaking、代码分割、压缩、Hash 和资源路径处理；
- 插件与配置体系：让框架编译、HTML 注入、Mock、产物加工等能力同时参与 dev 和 build。

可以把它理解成：

```text
Vite = Dev Server + 模块转换/HMR + 依赖预构建
     + 生产打包器 + 资源处理 + 插件体系
```

Vite 在开发阶段的核心思路是利用浏览器原生 ESM。传统 bundle-based dev server 启动前要先遍历并打包整个应用；Vite 主要按浏览器请求转换业务源码，所以项目变大以后，启动和热更新不必总是等待全量 bundle。

但“Vite 开发阶段完全不打包”也不准确：第三方依赖仍然会做预构建，一方面把 CommonJS/UMD 转成 ESM，另一方面把依赖内部的大量小模块合并，避免浏览器产生过多请求。

### 2.2 什么是 Rollup

Rollup 是一个**JavaScript 模块打包器**。它从一个或多个入口出发构建模块依赖图，完成模块解析、代码转换、Tree Shaking、Chunk 切分和产物生成，把大量 ESM/CommonJS 模块输出成浏览器或 Node.js 可以消费的 bundle。

它擅长的是“如何生成高质量产物”，特别是基于 ESM 的静态分析和 Tree Shaking；但 Rollup 本身不是一套完整的应用开发环境。本地 HTML 路由、开箱即用的 Dev Server、依赖预构建和框架级 HMR，通常还需要插件或上层工具补充。

### 2.3 Vite 和 Rollup 的区别

| 对比维度 | Vite | Rollup |
|---|---|---|
| 工具层级 | 面向应用的开发与构建工具 | 底层 JavaScript 模块打包器 |
| 主要目标 | 同时解决本地开发体验和生产构建 | 生成可控、优化后的 bundle |
| 开发服务器 | 内置 Dev Server、代理、HTML 入口和 HMR | 核心能力不包含完整应用 Dev Server |
| 开发阶段 | 业务源码主要通过 ESM 按需转换，依赖会预构建 | 通常先从入口构建依赖图并生成 bundle |
| 生产阶段 | 调用底层 bundler，提供预设和资源处理 | 自己就是 bundler，直接负责 Tree Shaking、分包和输出格式 |
| 插件体系 | API 是 Rollup 插件 API 的超集，还增加 dev/HMR 钩子 | 提供底层构建生命周期钩子 |
| 典型使用 | Web 应用、复杂本地联调、多环境构建 | 类库打包，或需要直接控制 bundler 的底层场景 |

因此，二者不是完全对立的竞品关系。传统 Vite 的典型架构是：开发阶段使用原生 ESM并借助 esbuild 做依赖预构建，生产阶段使用 Rollup 打包。Vite 负责上层工程体验，Rollup 负责生产 bundle。

```text
传统 Vite
开发：Dev Server + Native ESM + esbuild 依赖预构建
生产：Vite 配置/插件 ──调用──> Rollup ──输出──> JS/CSS/HTML
```

### 2.4 Rolldown 又是什么，为什么本项目不能只讲 Rollup

Rolldown 是用 Rust 编写、兼容 Rollup/Vite 插件接口的 bundler，目标是把过去 Vite 内部的 esbuild 和 Rollup 两条链路逐步统一。它使用 OXC 完成解析、语法转换和压缩等工作，同时保留 Tree Shaking、代码分割以及 Rollup 生态兼容能力。

当前项目的主应用使用 `vite@8.0.0-beta.2`，`vite.config.ts` 已经配置 `rolldownOptions` 和 `advancedChunks`。因此本项目更准确的表述是：

```text
当前项目
Vite：上层开发服务器、配置、插件和构建编排
Rolldown：Vite 8 下层的模块图分析、Tree Shaking、分包和产物生成
OXC：解析、语法转换和部分压缩能力
@baidu/vite-plugin-san：把 .san SFC 接入这套工具链
```

仓库根目录仍然存在 `rollup` 依赖，不代表 `packages/chat-search` 主应用当前仍由 Rollup 完成生产打包；Monorepo 中的历史脚本、其他子包或插件仍可能使用它。判断主应用实际链路，要看其 Vite 版本以及配置使用的是 `rollupOptions` 还是 `rolldownOptions`。

### 2.5 为什么这个项目选择 Vite，而不是继续直接维护 Rollup

这里不是因为“Vite 的 Tree Shaking 一定比 Rollup 好”，而是两者解决的问题范围不同：

1. **研发链路更完整。** 项目需要本地页面路由、BaseData 注入、Mock、后端代理、PC/Wise 切换和 HMR，Vite 提供了统一承载这些能力的 Dev Server 和插件生命周期。
2. **避免开发时全量重打包。** 业务源码按需转换，修改一个模块时通过 HMR 更新受影响边界，更适合模块数量持续增长的大型应用。
3. **没有放弃生产构建控制力。** Tree Shaking、动态 import、Chunk 分组、Hash、压缩和资源地址仍然可以通过底层 bundler 配置精细控制。
4. **迁移成本可以拆开。** Vite 插件接口延续 Rollup 约定，再通过 `@baidu/vite-plugin-san` 兼容存量 San SFC，不需要为了换构建工具同时重写业务组件。
5. **当前 Rolldown 进一步降低生产构建成本。** Rust/OXC 工具链提升模块分析和转换速度，并减少过去开发、生产两套底层工具行为不一致的问题。

面试时可以用一句话收口：

> Rollup 主要回答“代码最终怎么被打成高质量产物”，Vite 还回答“开发时怎么启动、转换、热更新和联调”。传统 Vite 生产构建本来就使用 Rollup，而我们当前 Vite 8 项目进一步使用 Rolldown 统一底层链路，所以这不是简单地用 Vite 替换 Rollup，而是从单一打包配置升级成完整的开发构建体系。

## 3. 90 秒面试回答

> 这个项目早期基于 Rollup，随着业务和依赖增长，构建、watch 和本地联调成本越来越高。我们迁移到 Vite 8 的 Rolldown/OXC 工具链，但难点不只是换配置，因为项目使用 San SFC、同时输出 Wise、PC、分享页、极速版和 Hybrid 产物，还要兼容服务端注入 BaseData。
>
> 我把优化拆成三层。研发侧通过 Vite Dev Server、自定义 HTML 注入插件、本地 Mock、默认代理和 RD 环境切换，让开发不再依赖远程开发机。构建侧使用 `advancedChunks` 按稳定 vendors、Cosmic、Marklang、图表和 diagram 依赖分组，并让 search/share 共享 common chunk；低频的 ECharts、Lottie、云 SDK、Cosmic Agent UI 和 Mermaid 等继续动态加载。加载侧又不能盲目懒加载，所以对高频代码高亮 Chunk 注入 modulepreload，对 PC 图标字体做 preload，避免首屏请求瀑布。
>
> 兼容方面我们把目标收敛到 ES2015，不启用 legacy 双产物，语法转译交给 OXC，API 兼容由显式 polyfill 负责。远程 watch 不压缩、不加 hash，线上才压缩并生成 hash，同时关闭 gzip 体积计算减少构建开销。技术串讲记录的专项结果包括 Wise 主应用 prod build 从 90 秒降到约 2 秒、首屏 JS 从 6.8MB 降到 5.2MB；但这些是当时特定入口和环境的历史口径，正式面试时我会明确范围，不说成当前全 monorepo 实测。

<details>
<summary><strong>展开查看：Vite 迁移、分包、懒加载、兼容性和指标的完整分析</strong></summary>

## 4. 原来的问题是什么

技术串讲记录的迁移背景包括：

- Rollup 构建和 watch 随业务增长持续变慢；
- 本地不能完整预览，需要依赖开发机；
- 构建模式继承历史子 Bot 和 ESL 集成，自动化能力弱；
- 首屏 JS/CSS 体积较大，业务改动后用户重复下载较多资源。

这里有两个不同目标，不能混为一句“构建慢”：

```text
研发时性能：dev 启动、HMR/watch、Mock、代理、联调
用户侧性能：入口体积、缓存复用、请求数量、首屏瀑布
```

Vite/Rolldown 能直接改善前一类，但后一类仍然需要分包、懒加载和依赖治理；换工具不会自动让首屏变小。

## 5. 当前工具链如何落地

### 5.1 Vite 8、Rolldown/OXC 与 San SFC

【当前实现】`packages/chat-search` 使用 `vite@8.0.0-beta.2`，配置入口使用 `rolldownOptions` 和 `advancedChunks`。项目又保留大量 `.san` 单文件组件，因此使用团队适配的 `@baidu/vite-plugin-san` 继续支持 San SFC。

这部分迁移的难点在于：成熟业务不能为了换工具一次性重写全部组件。先通过插件兼容存量 `.san`，让工具链迁移和业务语法迁移解耦；新模块再逐步采用 `index.ts + index.module.less`。

当前根依赖中的 San 已经是 `^3.15.4`。技术串讲中的“升级到 3.15.3”属于迁移当时的历史节点，不应当作当前精确版本。

### 5.2 多平台不是同一份 bundle 运行时判断

项目分别构建 Wise、PC 和 Wise-Speed：

```text
Wise       → search.html / share.html / static/
PC         → search-pc.html / share-pc.html / static/pc/
Wise-Speed → search-speed.html
```

原因是 Cosmic 组件存在 PC/Wise 不同产物。Vite 在构建时通过 alias 把相同 import 指向对应平台版本，而不是把两套组件都打进 bundle 后在运行时判断。这样减少无效代码，但代价是需要多次构建。

## 6. 研发体验：本地开发不再依赖开发机

【当前实现】Vite Dev Server 支持三种数据来源：

| 模式 | 配置 | 用途 |
|---|---|---|
| 本地 Mock | `MOCK=true` | 快速复现固定 SSE/接口，不消耗模型 token |
| 默认代理 | 不配置 SERVER | 代理到分级环境，适合日常开发 |
| RD 联调 | `SERVER=...` | 连接指定后端开发环境 |

`vite-search-dev-plugin.ts` 不只是一个 proxy，它还要：

- 根据 UA 把 `/search` 路由到 Wise 或 PC HTML；
- 从远端页面抽取 `aiTabFrameBaseData` 并注入本地 HTML；
- 兼容分享页不同的数据占位方式；
- 在开发环境处理 ETag，避免 HTML 已被改写但 Vite 错误返回 304；
- 支持 preview 和子路径会话路由；
- 在构建阶段检查 `chat-huabu`、extension 等依赖边界。

这就是一个值得讲的难点：AI 助手 HTML 不是纯静态入口，它依赖服务端数据注入。只有把线上 HTML 契约在 Dev Server 里模拟出来，本地开发才真正可用。

## 7. 产物优化：不是分得越细越好

### 7.1 稳定依赖和业务代码分离

【当前实现】`advancedChunks.groups` 按依赖稳定性和加载时机分组：

- `vendors`：San、router/store、Boxx、SSE/Stream 等稳定基础依赖；
- `@baidu/marklang`：Markdown 主库；
- Cosmic、ECharts、Lottie、云 SDK 等高优先级独立 Chunk；
- Mermaid、Markmap、Katex、D3 等 diagram 依赖单独分组；
- `common`：search 和 share 两个入口共享、且达到阈值的代码。

目标包括：

1. 业务代码更新时不让稳定 vendors 的 hash 一起变化；
2. 多入口共享依赖只下载一次；
3. 大型低频能力不进入首屏；
4. 相关依赖聚合，避免产生大量极小请求。

技术串讲早期描述为“交给构建工具自动拆包”，但当前代码已经演进为显式 `advancedChunks` 分组和优先级。面试应以当前事实为准：底层 Chunk 图由 Rolldown 生成，业务层仍通过规则约束稳定边界。

### 7.2 运行时按需加载

当前代码中的典型动态依赖包括：

```typescript
window.getEcharts = () => import('echarts');
import('lottie-web');
import('@baiducloud/sdk');
import('@baidu/cosmic-agent-ui/chat-view-core');
import('mermaid');
import('markmap-view');
```

例如 Cosmic Agent UI 通过 `getUiSdk()` 在实际需要时才加载，而不是回答页启动时预加载完整 SDK；Hector 也在 `chat-start` 后异步插入远程脚本，不进入主包同步执行链路。

### 7.3 为什么懒加载后还要 preload

代码拆出去会减小入口，但高频能力如果等运行时 `import()` 才被发现，会形成：

```text
入口执行 → 注册 Markdown 插件 → 发现代码高亮依赖 → 请求 Chunk → 执行
```

【当前实现】`vite-dynamic-chunk-preload-plugin.ts` 在构建阶段取得带 hash 的真实文件名，对高频的 `marklang-rehype-highlight` Chunk 向 HTML 注入 `modulepreload`，使下载与入口解析并行。

这是更完整的性能思路：

- 低频依赖：保持懒加载；
- 高频但不应阻塞打包的依赖：代码分包，同时提前预载；
- 首屏强依赖：进入同步依赖图。

PC 端还通过 `vite-cos-icon-preload-plugin.ts` 解析图标 CSS 中的字体 URL，优先 preload woff2/woff，减少图标字体首次出现时的闪烁。

## 8. 兼容性：语法转译和 API Polyfill 必须分开

【当前实现】构建目标为 `es2015`，不启用 `@vitejs/plugin-legacy`。项目明确支持：

- Chrome ≥ 64；
- Firefox ≥ 67；
- Safari ≥ 11.1；
- Edge ≥ 79；
- 不支持 IE。

选择不启用 legacy 的原因不只是“旧浏览器少”，还包括构建成本。当前配置注释记录，启用 legacy 后构建耗时曾从约 10 秒增加到 40 秒，因为它会引入 Babel 和额外产物。

同时必须说明：

```text
OXC/esbuild target → 负责语法降级
@searchfe/polyfills → 负责 Promise、AbortController 等运行时 API
```

项目在 `src/client/base.ts` 显式引入 `@searchfe/polyfills/config_output/all.js`。新的 Web API 仍需要单独检查支持范围，不能因为设置了 `target: es2015` 就认为所有 API 都被补齐。

## 9. 构建速度还有哪些具体处理

【当前实现】除了 Rolldown/OXC，本项目还做了：

- `reportCompressedSize=false`：不在构建阶段为大型产物逐一计算 gzip 体积；
- Remote watch 不压缩、不加 hash：减少重复构建成本，并保持资源 URL 稳定；
- Online 才启用压缩和 hash：保证线上体积与缓存安全；
- OXC/Rolldown 负责主要编译压缩，最后的 Terser 插件只做有限模板字符串处理，不再完整 Babel 转译；
- `optimizeDeps` 排除整个 Cosmic Agent UI，同时显式预构建其中的 `plyr`、`rangetouch`，兼顾大型包按需加载和内部 CommonJS 依赖的开发稳定性；
- `searchSpeedPlugin` 为特殊极速场景把 CSS 和入口 JS 内联到 `search-speed.html`，用更大的 HTML 换取减少首屏资源请求；
- Hybrid 插件复用同一次 Wise 构建生成离线包，不维护第二套业务编译链路。

## 10. 怎么准确讲优化结果

如流技术串讲记录的是迁移专项当时的历史结果：

| 指标 | 文档记录 | 回答时必须限定的口径 |
|---|---|---|
| 首屏 JS | 6.8MB → 5.2MB | 当时助手首屏资源，不代表当前版本 |
| 首屏 CSS | 1.4MB → 1.1MB | 需要说明统计的是原始、压缩还是传输体积 |
| 缓存使用 | 增加 50% | 指标定义不清时不要直接引用 |
| Wise prod build | 90s → 2s | 文档注明对话页＋分享页示例，不是全 monorepo build |
| dev build | 35s → 1.7s | 依赖机器、缓存和构建入口 |
| watch | 13s → 1.7s | 应说明是单次增量构建口径 |

正式面试最好准备一张统一环境下的对比表：Node 版本、机器、是否冷缓存、入口、构建命令、产物压缩口径。没有这些信息时，把数字说成“专项文档记录的历史结果”，不要说成现场刚测或所有 package 的普遍结果。

## 11. 方案的风险和取舍

- Chunk 分得过细会增加请求数、运行时调度和模块预加载开销；
- vendors 过大又会降低并行度，并让一处依赖升级造成整块缓存失效；
- 手工分组需要随着依赖图演进维护，优先级不当可能把大型依赖吸入错误 Chunk；
- preload 太多会和真正关键资源争抢带宽，因此只能预载高频依赖；
- ES2015 目标降低了转译成本，但新增 API 必须做浏览器审计；
- Vite 8 beta、Rolldown 和自研 San 插件需要持续处理工具链兼容；
- Search-Speed 内联资源减少请求，但失去独立资源缓存，不适合所有页面；
- 开发环境的数据注入插件越接近线上，维护成本越高，需要契约测试防止线上模板变更后本地失真。

## 12. 面试官可能继续问什么

### 为什么不把所有依赖都做动态 import

> 动态 import 能减小入口，但会把成本变成运行时请求和执行瀑布。我的原则是按访问概率和首屏依赖分层：低频大型能力懒加载，高频动态能力用 modulepreload，强依赖保留同步加载，再通过数据验证调整。

### 为什么不用 legacy 支持更多浏览器

> 兼容范围应该由真实用户和运行能力决定。目标浏览器已经覆盖实际可用环境，低版本即使语法转译成功也可能因 CSS、WebView API 等问题不可用；legacy 会额外生成产物并显著增加构建时间，所以项目选择 ES2015 加显式 API polyfill。

### 怎么保证拆包真的提升了缓存

> 不能只看 Chunk 数。我会比较版本间 hash 变化率、重复下载字节、入口传输体积、请求数和 FCP/FMP。稳定 vendors 与业务 Chunk 分离后，普通业务发布不应让基础依赖重新下载；如果 vendors 经常变化，说明分组边界或依赖版本仍需治理。

### Vite 为什么会比原 Rollup 快这么多

> 不是因为名字换了，而是开发阶段用原生 ESM 和依赖预构建减少整包等待，生产构建又使用 Rust 实现的 Rolldown/OXC；同时我们收敛兼容目标、减少历史插件、关闭非必要 gzip 统计，并为 Remote watch 关闭压缩。最终收益是工具链和配置共同作用。

## 13. 参考资料

- [Vite 官方文档：Why Vite](https://vite.dev/guide/why.html)：Vite 的 ESM 开发模式、依赖预构建、HMR，以及从 Rollup/esbuild 向 Rolldown 统一工具链的演进；
- [Rollup 官方文档：Introduction](https://rollupjs.org/introduction/)：模块打包器、ESM 静态分析和 Tree Shaking；
- [Rolldown 官方文档：Introduction](https://rolldown.rs/guide/introduction)：Rolldown 在 Vite 中的定位、Rollup 插件兼容和 OXC/Rust 工具链；
- [内部技术串讲：vite-rolldown](https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/76YVMWYfJM/YTLhlw6TTE/owQVkf13Ja1quy)：本项目迁移背景、历史指标和优化项。

</details>

---
