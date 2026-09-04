# ChatSearch：Hybrid 离线包与首屏优化

> **难点一句话：**Hybrid 不是“把 H5 放进 WebView”这么简单，而是同时管理 Native、WebView/Bridge 和 Web 应用三层。当前项目最难的是：静态资源要从本地秒开，动态 BaseData 又必须保持实时正确；离线包还要支持版本治理、原子更新、线上降级和紧急回滚。

| 面试官问什么 | 优先看哪几节 |
|---|---|
| “你怎么理解 Hybrid？” | 第 1、7 节 |
| “你在这个项目里具体做了什么？” | 第 4、5、8～11 节 |
| “离线包如何更新和回滚？” | 第 6、12 节 |
| “哪些已经实现，哪些只是方案？” | 第 4、14 节 |
| “给我一段完整回答” | 第 17 节 |

## 1. 面试先说清：我对 Hybrid 的理解

Hybrid App 是介于纯 Native 和纯 Web App 之间的混合开发模式。它不是某一个框架，而是一套运行架构：

```text
┌──────────────────────────────────────────┐
│ Web 应用层                               │
│ San.js / TypeScript / CSS / 业务状态      │
├──────────────────────────────────────────┤
│ WebView 运行与适配层                      │
│ JSBridge / URL 拦截 / 离线资源映射 / UA    │
├──────────────────────────────────────────┤
│ Native 平台层                            │
│ iOS / Android / 登录 / 相机 / 分享 / 存储 │
└──────────────────────────────────────────┘
```

几个容易混淆的概念要分开：

- **WebView**只是承载网页的运行容器；
- **JSBridge**解决 Web 与 Native 的能力通信；
- **离线包**解决 HTML、JS、CSS 等静态资源的加载速度与弱网可用性；
- 三者组合起来，再加上版本、降级和监控体系，才构成可上线的 Hybrid 方案。

### 为什么选择 Hybrid

| 维度 | 纯 Native | 纯 Web | Hybrid |
|---|---|---|---|
| 跨平台成本 | iOS/Android 分别开发 | 一套代码 | 主业务一套 Web，少量端能力分别适配 |
| 发布速度 | 依赖发版和用户升级 | 可快速发布 | Web 部分可快速更新，端能力仍随客户端发版 |
| 原生能力 | 最完整 | 浏览器能力受限 | 通过 Bridge 调用 Native |
| 首屏性能 | 通常最好 | 依赖网络资源 | WebView 预热 + 离线包可接近 Native |
| 一致性成本 | 双端代码容易出现差异 | 浏览器兼容问题 | 需要额外治理 Bridge、离线包和版本兼容 |

对 Chat Search 这种页面，Hybrid 的价值比较明显：业务迭代频繁，回答区大部分是 Web 擅长的文本和结构化卡片，同时又需要登录、分享、图片选择、多 Tab、TTS 等端能力。项目通过 `@baidu/boxx` 对端能力做统一封装，调用前使用 `canIUse` 判断，低版本或端外环境必须有降级路径。

Hybrid 也不是所有场景的最优解。高帧率动画、重度图形、实时音视频、对系统底层能力依赖很强的页面，Native 通常更合适；内容展示、表单、AI 对话和高频业务迭代更适合 Hybrid。

## 2. 当前项目的业务背景

普通 Web 页面访问 `/search` 时，由服务端页面层返回 HTML，并把本次请求的 `frameBaseData` 注入 `<head>` 中的 JSON Script。页面主体仍由客户端 San 渲染，前端启动时可以同步读取初始化数据：

```text
GET /search
└── text/html
    ├── <head>
    │   ├── aiTabFrameBaseData
    │   │   ├── user/token/lid
    │   │   ├── model/chatParams
    │   │   ├── experiments
    │   │   └── theme/switch
    │   └── search 客户端 Bundle
    └── <body><div id="app"></div></body>
                      ↓
                San CSR 挂载页面
```

所以普通 Web 的准确模式是“服务端注入启动数据＋CSR 渲染”，不是客户端启动后再请求一次 frameBaseData，也不是完整 SSR。

Hybrid 为了优化端内首屏，把 `search.html`、入口 JS 和 CSS 预先下载到 App 本地，由 WebView 的资源拦截层映射成本地文件。这样静态资源不用每次经过 DNS、TCP/TLS、CDN 下载。

但是离线 HTML 是构建时生成的，不能固化某个用户打开页面时才确定的 token、模型、登录态、实验分组和推荐问题。这形成了项目里真正的性能与正确性冲突：

```text
静态资源：越稳定、越本地越好
动态数据：越实时、越与用户绑定越好
```

## 3. 难点具体发生在哪里

### 难点一：静态资源可以离线，动态数据不能离线

如果把 BaseData 固化进离线包，它会过期，严重时还会串用户；如果每次都等待网络 BaseData 才启动所有模块，离线包的首屏收益又会被动态接口抵消。

### 难点二：启动链路从同步数据源变成异步数据源

普通 Web 模式下，很多初始化逻辑默认 `getFrameBaseData()` 立即有值。Hybrid 改为异步获取后，可能出现：

- Store 初始化拿不到 token、模型和实验；
- ChatStream 早于 BaseData 初始化；
- 主题和 Body Class 使用空数据，发生闪烁；
- 推荐问题重复发请求；
- BaseData 返回后全量重建 Store，覆盖已经解析好的路由、Query 和页面状态。

所以这不是“把一个同步函数改成 async”，而是给整个启动链路增加数据就绪协议。

### 难点三：离线包会产生版本碎片

不同客户端版本可能预置不同的 Chat Search 包，后台静默下载也不保证所有用户都升级成功。因此线上同时存在多个 Web 资源版本，后端接口必须兼容长尾版本；发生 breaking change 时，还要能禁止过旧资源继续运行。

### 难点四：更新失败不能破坏当前可用版本

下载中断、磁盘不足、解压失败、MD5 不一致、两个 WebView 同时触发更新，都可能造成半包。如果直接覆盖当前目录，用户下次打开会得到缺 JS、缺 CSS 的损坏页面。

### 难点五：缓存速度与数据正确性冲突

缓存命中越积极，首屏越快；但登录态、模型配置和实验分组发生变化后，旧缓存可能已经不适用。实验串组尤其隐蔽：页面看起来正常，实际却进入了错误策略。

### 难点六：首启失败不能让第一条消息永久不可用

冷启动时 BaseData 网络请求可能失败。如果失败结果成为整个页面生命周期的最终状态，用户稍后点击发送时即使网络恢复，仍可能因为没有 token 而失败。

## 4. 整体架构与责任边界

```text
前端构建
  ├─ 正常 Web：dist/search.html + CDN 静态资源
  └─ Hybrid：hybrid-static.zip + manifest.json
                            ↓
Hoth /api/manifest 返回版本、URL、MD5、max-age
                            ↓
Native 离线包管理器
  ├─ 判断本地版本能否使用
  ├─ 后台下载、校验、解压、原子切换
  └─ 本地包不可用时打开 Web URL
                            ↓
WebView 加载本地 search.html
  ├─ meta 标记 Hybrid 模式与资源版本
  ├─ BaseData localStorage 缓存
  ├─ BaseData 网络刷新
  └─ 数据 Ready 后初始化 ChatStream
```

责任边界必须说准确：

| 模块 | 当前仓库可核对的实现 | 不在当前前端仓库中的职责 |
|---|---|---|
| 构建层 | ZIP、package.json、manifest、版本和 MD5 | CDN 上传平台 |
| Hoth 层 | `/api/manifest` GET/POST 接口 | 客户端灰度配置平台 |
| Native 层 | 前端只定义协议和接收模式 | 下载、解压、目录切换、`minVersion/forceUpdate` 决策 |
| Web 层 | Hybrid 识别、BaseData SWR、Store 同步、ensureToken | Native WebView 池和磁盘管理 |

面试时可以讲参与了端到端方案设计，但不能把 Native 端代码说成当前前端仓库内的实现。

<details>
<summary><strong>展开查看：Hybrid 完整实现细节（构建、Native 更新、Bridge、BaseData、回滚与监控）</strong></summary>

## 5. 构建期：当前代码实际如何生成离线包

实现文件是 `packages/chat-search/scripts/vite/vite-hybrid-plugin.ts`。

### 5.1 触发方式

当前 `package.json` 没有独立的 `build:hybrid` 脚本。真实逻辑是在 Wise 的 online 或 remote 构建中启用 `hybridPlugin`，一次构建同时产出 Web 和 Hybrid，避免维护两套构建链路。

### 5.2 只收集首屏同步依赖

插件从 `search` 入口 Chunk 开始，递归遍历同步 `imports`，并读取每个 Chunk 的 `viteMetadata.importedCss`。异步卡片和低频动态 Chunk 不全部塞进离线包，从而控制下载体积。

这意味着离线包优化的是“首屏必要资源”，不是宣称整个应用完全离线。异步资源仍需要 CDN 路径或业务降级。

### 5.3 一份 HTML 生成两种运行模式

Hybrid ZIP 内的 `search.html` 会注入：

```html
<meta name="hybridInfo" version="x.y.z" mode="hybrid" />
```

正常 Web 产物移除占位符。运行时 `getHybridInfo()` 读取这个 meta，得到：

```typescript
interface HybridInfo {
    isHybrid: boolean;
    version: string;
}
```

因此前端不通过 UA 或 URL 猜测资源来源，模式和资源版本都来自构建产物自身。

### 5.4 当前真实产物

```text
dist/
├── search.html                                  # 正常 Web 入口
├── config/manifest.json                         # Hoth 启动时读取
└── static/acc-packages/{version}/
    └── hybrid-static.zip
        ├── search.html                          # Hybrid 入口
        ├── package.json                         # URL 与本地资源映射
        └── static/...                           # 首屏同步 JS/CSS
```

当前 `manifest.json` 包含：

```json
{
    "name": "chat-search-hybrid",
    "version": "x.y.z",
    "max-age": "259200000",
    "hybridStaticUrl": "https://.../hybrid-static.zip",
    "hybridStaticMd5": "..."
}
```

其中 `max-age` 当前构建常量是 3 天。`package.json` 除版本和构建时间外，还维护页面 URL、静态资源 URL 和本地文件的映射，以及 html/js/css Content-Type，供 Native 请求拦截层正确返回资源。

### 5.5 版本生成

- 线下构建使用 `1.0.{timestamp}`，避免开发包与线上版本冲突；
- 线上构建从最近一次成功发布产物的 `manifest.json` 读取 SemVer；
- 兼容更新执行 patch + 1；
- `BREAKING_CHANGE=true` 时执行 major + 1；
- 上一版本获取或格式校验失败时中断构建，避免生成不可追踪版本。

### 5.6 manifest 服务

`packages/chat-search/src/server/controller/chat/hybrid.controller.ts` 在 Hoth 服务启动时读取一次 `dist/config/manifest.json` 并缓存在内存，通过 `/chat-search/api/manifest` 的 GET/POST 返回。这样每次请求不做磁盘 I/O；如果 manifest 缺失或解析失败，接口返回 503，而不是下发空版本让 Native 错误更新。

## 6. Native 端更新机制应该如何设计

这一部分来自技术方案，是前端与 Native 共同约定的运行协议；Native 实现不在当前仓库中。

### 6.1 版本决策不是简单比较“本地是否等于最新”

| 条件 | 本次打开使用什么 | 后台行为 | 原因 |
|---|---|---|---|
| 没有本地包 | Web/预渲染路径 | WebView 进入后或闲时下载 | 不让首次安装被整包下载阻塞 |
| 本地包有效 | 立即使用本地包 | 静默检查并更新 | 优先保证打开速度 |
| `localVersion < minVersion` | 强制走 Web | 下载合格版本 | 旧包可能与接口不兼容 |
| 超过 `maxAge` | 优先走 Web | 后台更新 | 避免用户长期停留在旧资源 |
| 落后版本过多 | 优先走 Web | 后台更新 | 控制版本碎片度 |
| `forceUpdate=true` | 直接走 Web | 等待故障处理 | 线上紧急熔断所有离线包 |
| 下载、校验或解压失败 | 保留旧包；旧包不可用则走 Web | 下次再重试 | 更新失败不能破坏可用版本 |

为什么需要 `minVersion`，不能只要求更新到 latest：因为最新包下载不保证成功，线上一定存在长尾版本。`minVersion` 表达的是“最低兼容边界”，latest 表达的是“期望收敛目标”，两者语义不同。

### 6.2 更新触发时机

- 无本地包的首次冷启不阻塞拉包；
- WebView 进入后静默检查；
- App 空闲时预下载；
- App 回前台时可以节流检查；
- 空闲期清理低于 `minVersion` 的过期包。

默认采用 stale package first：本次使用仍有效的旧包，后台准备新包，下次打开生效。只有强制过期或熔断时才牺牲本地速度走 Web。

### 6.3 必须原子更新，不能原地覆盖当前目录

推荐目录结构：

```text
chat-search-hybrid/
├── current_version.txt
├── 1.0.8/
├── 1.0.9/
└── .tmp-1.0.10/
```

更新流程接近：

```typescript
async function installPackage(manifest: Manifest): Promise<void> {
    await withSingleFlightLock(`chat-search:${manifest.version}`, async () => {
        const tempZip = await downloadToTemp(manifest.hybridStaticUrl);
        assertMd5(tempZip, manifest.hybridStaticMd5);

        const tempDir = await unzipToTempDirectory(tempZip);
        validatePackageJson(tempDir, manifest.version);
        validateRequiredFiles(tempDir, ['search.html', 'package.json']);

        // rename 必须在同一文件系统中完成，避免复制到一半暴露给读取方
        await atomicRename(tempDir, versionDirectory(manifest.version));

        // 最后一步才切换当前版本指针
        await atomicWriteCurrentVersion(manifest.version);
    });
}
```

需要保证四个不变量：

1. 下载和解压永远发生在临时路径；
2. MD5、版本和必需文件校验成功后才能暴露正式目录；
3. 最后才原子切换 `current_version`；
4. 新版本失败时，旧版本目录和当前指针不发生变化。

两个 WebView 同时更新时，要按“业务包 + 目标版本”做单飞锁；清理旧包必须避开当前正在被 WebView 使用的目录。通常保留“当前版本 + 上一个已验证版本”，便于失败回退。

## 7. Bridge：Hybrid 不只是离线包

离线包解决资源加载，JSBridge 解决 Web 与 Native 的能力边界。

### 7.1 Web 调 Native 的常见方式

| 方式 | 平台 | 特点 | 适用位置 |
|---|---|---|---|
| URL Scheme 拦截 | Android/iOS | 兼容性好，但需要编码、长度有限、异步回调 | 双端通用 fallback |
| `addJavascriptInterface` | Android | 调用直接、性能好，可同步返回；需版本与安全控制 | Android 主通道 |
| `WKScriptMessageHandler` | iOS | iOS 官方异步消息通道，安全性较好 | iOS 主通道 |
| prompt/alert 拦截 | Android/iOS | 可兼容同步调用，但体验和容量限制明显 | 特殊兼容路径 |

### 7.2 Native 调 Web

优先使用 `evaluateJavascript` 执行已约定的 JS 回调；低版本可使用 `loadUrl("javascript:...")` 兜底。`loadUrl` 会走完整 URL 解析流程，频繁调用容易堆积，因此不应作为高频主通道。

### 7.3 统一 Bridge 封装要解决什么

Android 可能是同步注入对象，iOS 是异步 `postMessage`。Web 侧应该统一成 Promise/回调协议：

```typescript
type BridgeMessage = {
    method: string;
    params: unknown;
    callbackId: string;
    protocolVersion: string;
};
```

关键治理点包括：

- callbackId 与回调 Map，完成后立即删除；
- 超时和页面销毁时清理，避免回调泄漏；
- method 白名单、参数校验和协议版本；
- 大数据和二进制不通过 URL Scheme 传递；
- Native 回调切回 WebView 所在线程；
- 调用前判断能力和端版本，失败时回退 Web 能力。

当前项目没有手写 Android/iOS 两套 Bridge，而是主要通过 `@baidu/boxx` 使用端能力。代码中的标准模式是：

```typescript
if (boxx.canIUse('search.open')) {
    boxx.search.open({url, title});
}
else {
    window.open(url, '_blank');
}
```

这体现了 Hybrid 的重要原则：端能力是增强，不应该让不支持该能力的端外浏览器或低版本客户端直接崩溃。

## 8. 运行期：BaseDataController 的 SWR 策略

`packages/chat-search/src/client/service/base-data.ts` 负责 Hybrid 动态数据。

`BaseDataController` 创建时同时做两件事：

1. 同步读取 localStorage 缓存；
2. 无论缓存是否命中，都立即发起 BaseData 网络请求。

所有消费者共享一个 `fetchPromise`：

| 场景 | 本次启动 | 网络请求作用 |
|---|---|---|
| 缓存有效 | 立即返回缓存，不阻塞启动 | 只刷新 localStorage，供下次使用 |
| 没有缓存 | 等待共享网络请求 | 为本次启动提供 BaseData |
| 缓存过期 | 清除缓存并等待网络 | 获取新数据 |
| Native 实验变量变化 | 清除缓存并等待网络 | 防止串实验 |
| localStorage 异常 | 忽略缓存 | 降级为纯网络路径 |
| 网络失败 | 返回 null | 其他模块降级，发 Q 前仍可恢复 |

这里是严格的 stale-while-revalidate 取舍：命中缓存后，网络返回的新数据默认不热替换本次 Store，避免页面运行中模型、实验或 UI 配置突然改变；它只更新下次启动所用缓存。

## 9. 缓存不能只看 TTL，还要看实验一致性

```text
cacheValid = Date.now() <= expireAt
          && isNaExpVarsEquivalent(cachedNev, currentNev)
```

- 过期时间优先使用服务端 `guidewords.baseDataExpire`；
- 没有服务端值时兜底 24 小时；
- 请求时把 Native 实验变量 `nev` 一起写入缓存；
- 当前 `nev` 与缓存不等价时立即清除；
- `0`、字符串 `"0"` 和字段缺失都表示未命中实验，归一化后等价；
- 真正命中的实验值发生变化时必须失效。

`chat-util` 只负责通用缓存读写，实验等价规则通过回调留在 `chat-search` 业务层，避免工具包反向依赖业务。

需要注意：BaseData 可能包含用户信息和 token。完整对象写入 localStorage 会扩大 XSS 后的暴露面，也可能在账号切换时残留。因此更严格的方案还应绑定用户标识、退出登录时清理，并评估敏感字段是否必须持久化；这属于当前方案需要持续关注的安全边界。

## 10. 异步初始化屏障与最小 Store 同步

`chat.main.san` 中的实际顺序是：

```text
chat-start
   ↓
await loadBaseData()
   ↓
setFrameBaseDataCache(data)
   ↓
syncFrameBaseDataStore()
   ↓
initChatStream()
   ↓
chatInit()
```

缓存命中时 `await` 几乎立即完成；未命中时它保证依赖 token、模型和 `chatParams` 的模块不会提前启动。

BaseData 返回后不全量重建 Store，而是复用 `getFrameBaseDataFields()`，只更新直接由 `frameBaseData` 派生的字段。这样路由、Query、当前页面状态和其他已经完成的异步结果不会被覆盖。

这里有一个面试中值得强调的边界：BaseData 请求失败时，`loadBaseData()` 会返回，ChatStream 仍然初始化。这个屏障保证“有数据时顺序正确”，而不是让接口失败造成永久白屏；真正发送消息时再做 token 兜底。

## 11. 首条消息、推荐问题和失败恢复

### 11.1 ensureToken 单飞兜底

发起对话前如果 Store 没有 token：

```text
等待首启 fetchPromise
        ↓失败
重新发起一轮带重试的 BaseData 请求
        ↓成功
更新内存缓存 + 最小同步 Store
        ↓
继续发送问题
```

多个并发发送共用 `ensurePromise`，避免同时刷新多次。请求的 `BASE_DATA_RETRY_TIMES=2` 表示首次加两次重试；最终失败仍进入正常错误链路，不能伪造 token 或无限等待。

### 11.2 复用 guidewords

BaseData 已聚合 `guidewords`，Hybrid 无 Query 首页优先直接复用。BaseData 没返回时才回退原 guidewords 接口；带 Query、百科半屏等特殊场景保留既有时序，避免为了统一而延迟 Query 上屏。

## 12. 发布、回滚和容量风险

### 正常更新

```text
发布 Web 与 Hybrid 同源产物
        ↓
Native 继续用有效本地包打开
        ↓
后台拉 manifest、下载并校验新包
        ↓
下次打开切换新版本
```

### 紧急回滚

1. 先回滚线上 Web 资源；
2. 下发 `forceUpdate=true`，让 Native 暂停使用本地离线包并走已回滚 Web；
3. 修复后发布更高版本离线包；
4. 将 `minVersion` 提升到修复版本，阻止有问题的旧包再次启用；
5. 观察版本收敛后关闭 `forceUpdate`。

回滚不能只考虑功能正确性，还要考虑流量。正常 Hybrid 命中会减少 `/search` 和静态资源请求，但增加 manifest 检查；`forceUpdate` 或 Hybrid 全量失效时，大量用户会同时回源 `/search`、manifest 和 CDN，最坏情况下相关请求量可能接近平时 Web 流量与更新流量叠加。

因此上线前要准备：

- manifest、Hoth `/search` 与 CDN 的容量预估；
- forceUpdate 分批或灰度能力；
- 最新版本占比、长尾版本占比、更新成功率监控；
- 下载、MD5、解压、切换、Web 降级分别打点；
- 回滚演练，而不是只在故障时临时操作。

## 13. 可观测性：怎么证明优化有效

当前前端日志会附带 Hybrid/Web 模式和资源版本，并记录 BaseData 等耗时。完整指标应分层观察：

| 层次 | 关键指标 | 要回答的问题 |
|---|---|---|
| 包管理 | manifest 成功率、下载/校验/解压/切换成功率 | 离线包能否稳定更新 |
| 版本治理 | 最新版本占比、长尾版本占比、低于 minVersion 占比 | 版本是否收敛 |
| 首屏性能 | FCP/FMP、P75/P95、本地包命中率 | 本地资源是否真正提速 |
| 动态数据 | BaseData 缓存命中率、接口耗时、失败率 | 首屏是否仍被接口阻塞 |
| 业务可用 | 首问成功率、token 补拉成功率 | 页面快是否同时可用 |
| 降级流量 | Web fallback PV、CDN/Hoth 峰值 | 熔断是否会冲击服务容量 |

只看平均 FMP 不够。Hybrid 优化主要改善长尾弱网体验，所以应重点看 P75/P95，并按资源版本、客户端版本、网络类型、Hybrid/Web 模式拆分。

## 14. 技术方案与当前代码的差异必须说准确

| 项目 | 技术方案中的早期描述 | 当前仓库事实 | 面试口径 |
|---|---|---|---|
| 压缩格式 | `hybrid-static.tar.gz` | `hybrid-static.zip` | 以当前 ZIP 实现为准 |
| 构建命令 | `pnpm build:hybrid` | 无该脚本；Wise online/remote 构建自动启用插件 | 说“一次构建双产物” |
| manifest 位置 | CDN 或 Hoth 待定 | 当前生成到 `dist/config`，由 Hoth `/api/manifest` 返回 | 说当前选择 Hoth 接口 |
| 资源有效期 | 示例 7 天 | manifest 当前为 3 天；BaseData 无服务端值时兜底 24 小时 | 区分资源包 TTL 与数据 TTL |
| 运行模式识别 | URL 下发 `hybridMode` | 前端主要读取 HTML meta | 以 `getHybridInfo()` 为准 |
| 端更新 | 方案描述了策略 | Native 代码不在本仓库 | 说“共同协议/端侧职责” |
| 预渲染 | 文档有“可抛弃”的设想 | 无本地包时仍需要 Web 或其他首启兜底 | 不做绝对承诺 |

## 15. 方案取舍与尚存风险

- SWR 会让用户在有效期内使用非最新 BaseData，这是用一致性窗口换启动速度；关键数据必须额外校验；
- 离线包只收首屏同步依赖，控制了包体，但异步卡片仍受网络影响；
- localStorage 可能因隐私模式、容量或清理失败，所有异常都要降级到网络；
- BaseData 缓存需要关注用户切换与 token 持久化的安全边界；
- Hoth 在进程启动时缓存 manifest，回滚 manifest 后需要配合服务重新加载或发布流程；
- MD5 能检测传输损坏，但不是防篡改签名；若威胁模型要求防篡改，应使用带公钥校验的签名；
- 技术方案中的原子写入仍需 Native 端真实实现和并发验证，不能只停留在目录约定。

## 16. 我重点保证的系统不变量

- 离线包只保存可跨用户复用的静态资源，不固化用户动态数据；
- Web 与 Hybrid 来自同一次构建；
- 未完整校验的新包永远不能替换当前可用包；
- `minVersion` 表示兼容底线，latest 表示收敛目标；
- 下载或解压失败必须保留旧包或回退 Web；
- 只有未过期且 Native 实验一致的 BaseData 缓存可以使用；
- Hybrid 在 BaseData 尝试完成后才初始化 ChatStream，但失败不能造成永久白屏；
- Store 只同步 BaseData 派生字段，不全量重置；
- 发 Q 前缺少 token 时有单飞恢复机会；
- 资源版本、更新状态、动态数据耗时和首问成功率都必须可观测。

</details>

## 17. 面试回答口径

### 90 秒完整回答

> 我理解的 Hybrid 不是简单在 App 里嵌一个 WebView，而是 Native、WebView/Bridge 和 Web 应用三层共同组成的运行体系。Bridge 负责端能力，离线包负责静态资源性能，版本、降级和监控负责让它可以长期上线。
>
> 在 Chat Search 中，离线包最难的地方是静态资源和动态数据的生命周期不同。构建侧我们从 search 入口递归收集同步 JS/CSS，一次构建同时产出正常 Web 和 hybrid-static.zip；ZIP 内有资源映射，manifest 提供版本、三天 max-age、下载地址和 MD5，Hoth 启动时读取并通过 `/api/manifest` 返回。Native 应先用有效旧包打开，后台下载到临时目录，完成 MD5、版本和文件校验后再原子切换；没有包、低于 minVersion、forceUpdate 或更新失败时走 Web 降级。
>
> 静态页面加载以后，token、模型和实验不能放进离线包，所以前端用 BaseDataController 做 SWR：同步读有效缓存，同时发起共享网络请求；缓存还要校验 TTL 和 Native 实验变量。ChatStream 初始化前 await BaseData，但接口失败不会阻塞页面；发 Q 前如果仍没有 token，再通过单飞 ensureToken 补拉。这样优化的不只是资源加载，而是同时处理首屏速度、数据正确性、首问可用、版本治理和故障回滚。

### 如果面试官问“这个方案真正难在哪里”

> 真正难点不是压一个 ZIP，而是四个一致性问题：第一，离线静态资源与用户动态数据不能混在同一生命周期；第二，多个客户端预置版本造成后端兼容和 minVersion 治理；第三，下载、解压和并发更新必须原子，不能产生半包；第四，forceUpdate 回退 Web 会造成流量突增，所以回滚必须和容量、灰度、监控一起设计。

### 如果面试官问“你们为什么不每次强制更新到最新”

> 因为强制等待新包会把网络和整包下载重新放回首屏关键路径，而且下载不可能百分之百成功。我们区分 latest 和 minVersion：有效旧包优先打开，后台静默更新；只有低于最低兼容版本、缓存严重过期或紧急熔断时才走 Web。这样把性能目标和兼容底线分开管理。

## 18. 参考方案

- [hybrid -- 技术方案](https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/pKzJfZczuc/e4Hs33ppbo/L6fofBxlYpV4fY)：离线包目录、更新模式、manifest、回滚、版本收敛与流量风险；
- [技术串讲 - hybrid](https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/76YVMWYfJM/YTLhlw6TTE/PuKjDPybsiL2VO)：Hybrid 三层架构、Bridge 双向通信、离线包基础原理、WebView 预热与调试。

---
