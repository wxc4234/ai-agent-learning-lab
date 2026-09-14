# 认证 BFF 如何安全转发 Cookie、查询身份和处理取消？

> 复习优先级：高 | 项目：AI Agent Learning Lab | 参考答案已整理、尚未模拟

## 参考答案与取舍

浏览器只请求同源 /api/auth/login，Next.js 服务端 fetch FastAPI。API_BASE_URL 不使用 NEXT_PUBLIC_。服务端收到 Set-Cookie 不会自动交给浏览器，必须 getSetCookie() 后逐条 append；不能按逗号拆分或合并，因为 Expires 自身含逗号。浏览器收到的是 BFF 响应，未设置 Domain 的 Cookie 归属 BFF 主机；JavaScript 无法读取 HttpOnly Cookie 或响应中的 Set-Cookie。

BFF 精确校验 Origin 后原样转发，不把所有请求伪装成可信来源。密码不 trim、不改大小写，业务校验仍由 FastAPI 负责。只检查 JSON 可解析，成功输出挑选安全身份字段，后端错误通过状态+错误码匹配固定安全文案。未知响应、缺少登录 Cookie、连接故障返回安全 502；成功与失败 no-store，错误响应不转发 Cookie。redirect=error 防止意外重定向携带登录请求。

AbortSignal.any 合并客户端取消与 10 秒超时；signal 同时覆盖 fetch 和响应正文读取。超时 504，取消内部使用非标准 499，断开的客户端未必能收到。取消不保证后端事务未提交，因此不自动重试登录，也不宣称数据库/网络原子性。

## 失败场景

- routes.ts 不符合 Next 路由文件约定，必须 route.ts；编辑器仍打开旧文件可能重新创建错误副本。
- Content-Type 拼成 applation/json 会被后端判 415；类型检查不能发现协议字符串错误。
- 只返回 JSON 而遗漏 Set-Cookie，页面看似登录成功但浏览器没有凭证。
- 只给 fetch 等待响应头设置超时，正文读取可能继续挂起。
- 把后端异常正文原样透传，可能泄露内部信息；复制全部成功字段也可能泄露凭证。

## 项目证据

- apps/web/src/app/api/auth/login/route.ts：登录 BFF 实现。
- apps/web/test/features/auth/login-route.test.ts：28 条 Node 测试，验证输入/来源、请求参数、安全错误、独立多 Cookie、异常上游、正文超时和取消。
- 2026-09-14：认证 28/28、原有前端 71/71，TypeScript/ESLint 通过。Node 测试显式 ESM，无模块类型推断警告；Next 类型生成有 Rosetta 性能提示。
- 独立 PostgreSQL 库/私有 schema + 临时 Next.js/FastAPI + Playwright 无头 Chrome 实测：同源登录、Cookie 保存/HttpOnly/Lax/host/path/expiry、刷新保持、Cookie 对应身份、错误密码 401、错误来源 403；所有临时服务/数据库清理。临时 Next 使用与仓库一致的 route.ts 副本与同版本依赖，后端使用仓库应用；未在开发业务表写测试账号。这是登录 BFF 课的验证；当前用户查询的后续证据见下文，后续登录页与登出 BFF 的验证见下文。


## 当前用户查询：为什么浏览器有 Cookie，后端仍可能返回 401？

> 复习优先级：高 | 参考答案已整理、尚未模拟

浏览器会向同源 BFF 发送 Cookie，但 BFF 的服务端 fetch 是另一个请求，不会自动继承浏览器 Cookie。GET /api/auth/me 显式提取 agent_session，并通过 Cookie 请求头交给 FastAPI /auth/me；身份只来自后端验证结果，不能信任 URL 或前端提交的 external_id。

本项目只允许转发 agent_session，其他 Cookie 留在 BFF 边界。按分号分段、首个等号分离名称和值，令牌必须为 43 位 URL 安全字符；缺失、非法、同名重复统一 401，避免不同解析器对重复值选择不一致。不 URL 解码、不接受引号令牌；这是本项目令牌协议的约束，不是通用 Cookie 解析器。此处格式检查不代表会话有效，过期/撤销仍由数据库决定。

GET 不需要 JSON 正文，也不强制登录 POST 的 Origin 校验；接口只读且不开放跨域读取。成功只输出 external_id/username，401/500 通过状态与错误码白名单映射安全文案，所有响应 no-store，不转发 Set-Cookie，不续期或清 Cookie。连接失败或不可解析 JSON 当前都映射 current_user_backend_unavailable（502），可解析但结构/状态不合法映射 invalid_backend_response（502）。不能把 500/502 当成“确定未登录”后直接清除用户会话。

项目证据：apps/web/src/app/api/auth/me/route.ts 与 test/features/auth/me-route.test.ts。35 条新测试覆盖 Cookie 解析/转发隔离、安全字段、上游错误、响应头与正文阶段的超时及取消；认证共 63 条、聊天 71 条通过，TypeScript/ESLint 通过。后端完整 469 条通过，DeprecationWarning 作为错误运行且零警告，Ruff 通过。

真实浏览器证据（2026-09-14）：隔离 PostgreSQL + 临时 Next/FastAPI + Playwright Chrome，登录后刷新，经同源 /api/auth/me 获取相同安全身份；重放已撤销会话、数据库中过期会话、缺失 Cookie 都返回 401，读取操作不清除 Cookie。临时资源自动清理，未写开发业务表。


## 登出为什么要先撤销服务端会话，再清浏览器 Cookie？

> 复习优先级：高 | 参考答案已整理、尚未模拟

只删除 Cookie 不会使已复制的令牌失效；只撤销数据库记录则会让浏览器持续发送失效 Cookie。POST /api/auth/logout 精确校验 Origin，将唯一的登录令牌交给后端；后端提交撤销后返回 204 和清除指令，BFF 验证 agent_session、Max-Age=0、Path=/、无 Domain 后逐条透传 Set-Cookie。204 无正文，不能调用 json()；返回 new Response(null, {status: 204})。

缺失或单个格式错误的 Cookie 不转发令牌，但仍请求后端获得清除指令；重复同名 Cookie 返回 400，避免选择歧义令牌。已撤销、已过期、无记录均可正常完成登出；撤销仅针对当前令牌，其他会话有效。清除指令检查是与本项目后端的协议校验，不是通用或对恶意上游的完整 Cookie 验证器。

后端 403/500、非法响应、连接失败、超时和取消均不清 Cookie。超时可能发生在后端已经提交之后，因此客户端无法凭网络错误判断会话是否仍有效；不把错误伪装成成功，不自动重试。读取身份的 401 与服务故障保持分类。

项目证据：apps/web/src/app/api/auth/logout/route.ts 和 test/features/auth/logout-route.test.ts。新增 39 条测试，覆盖请求隔离、重复/缺失/非法 Cookie、清除属性、空 204、错误脱敏、超时取消与不重试；认证总计 102 条、聊天 71 条通过，TypeScript/ESLint 通过，后端 469 条零警告、Ruff 通过。学习者核心实现与参考一致，无需修改。

浏览器证据（2026-09-14）：独立 PostgreSQL/私有 schema、临时 Next/FastAPI 与 Playwright Chrome 验证同源登录→查询→登出→查询 401，浏览器 Cookie 删除、旧令牌重放无效、另一会话有效、重复/过期会话登出成功、Origin 拒绝保留会话。临时资源自动清理；未写开发业务表。


## 认证页面如何避免错误状态和异步竞态？

> 复习优先级：高 | 参考答案已整理、尚未模拟

认证状态用可辨识联合区分 checking、anonymous、authenticated（携带安全身份）、unknown；401 表示未登录，网络/服务失败表示状态无法确认，不能将两者混淆。登录或退出的网络失败可能发生在后端提交之后，页面引导重新查询，不能直接宣称成功或盲目重试写请求。

pending 控制加载与禁用；useRef 保存当前 AbortController，同步锁住重复请求，弥补状态更新在下一次渲染才反映的时差。每次异步结果写状态前核对当前控制器，卸载时先清引用再 abort；即使底层请求忽略取消并晚到，旧结果也不能覆盖新状态。finally 同样核对控制器，不能替新请求解除加载状态。Effect 的微任务有 disposed 清理标记，适配开发模式的重复 setup/cleanup。

页面只请求同源 BFF，浏览器自动携带 HttpOnly Cookie；不把令牌写入前端状态或存储。密码原样提交、结果后清空，不存入 localStorage 或日志。当前 React 类型声明已弃用 FormEvent，表单提交使用从 react 导入的 SubmitEvent<HTMLFormElement>，仍需 preventDefault。

项目实现：apps/web/src/app/login/page.tsx。页面门禁和用户资源授权尚未接入；单独登录页并不意味着聊天 API 已受保护。

页面证据（2026-09-14）：test/browser/login-page.mjs 的 10 个浏览器场景完整通过，覆盖真实账号流程、移动视口无横向溢出、请求失败恢复、重复提交、超时、卸载取消与旧响应隔离。取消测试等待 React Effect 清理并禁用超时信号，避免把 URL 改变误当清理完成，或把超时误当卸载取消。隔离启动器 run-isolated.py 复用 PostgreSQL 测试库，清理临时服务与数据；认证 102 条、聊天 71 条、TypeScript/ESLint 通过。

## 首页门禁为什么不能替代后端授权？

> 复习优先级：高 | 参考答案已整理、尚未模拟

AuthGate 先查询 /api/auth/me；只有 200 且身份结构有效才渲染 ChatPanel，401 跳转登录，服务异常留在当前页重试。这可以避免查询前挂载聊天组件，但客户端代码可被绕过，不能阻止直接请求聊天 API。后端还必须验证身份，并对 Conversation/Run 等资源按 owner 过滤；“已登录”也不等于可以访问其他用户资源。

本课使用 router.replace，替换中间跳转历史。登录页只在身份已确认、pending 结束且 next 参数恰好一个、值为 / 时跳转到固定首页；不直接使用用户输入的 URL。直接访问 /login 保留账号与退出界面。外部 URL、协议相对地址、javascript:、登录页自身以及重复参数都不触发返回，避免开放重定向或循环。前端门禁只处理进入页面时的身份查询，不代表已经完成停留期间的过期检测和跨标签退出同步。

项目代码：src/features/auth/components/auth-gate.tsx、src/app/page.tsx 和 src/app/login/page.tsx。

门禁工程证据（2026-09-14）：隔离浏览器新增 8 个场景，全套 19/19 通过；测试启动器复制真实首页，覆盖登录返回、刷新与退出后重新进入、服务/网络/非法身份故障、pending 不挂载聊天、同步防重复重试、超时和卸载取消、旧响应不导航、next 白名单。认证 102 条、聊天 71 条、TypeScript/ESLint 通过。核心未改动，独立测试库与临时服务清理完成。

## 已登录页面发起流式聊天，BFF 如何传递身份并区分错误？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：浏览器只请求同源 /api/chat/stream。BFF 精确验证 Origin、要求 JSON，并只转发唯一且格式有效的 agent_session，不复制其他 Cookie 或客户端伪造身份头。服务端 fetch 明确设置 Cookie 与原始 Origin，保留 request.signal、no-store 和 redirect:error；服务端 fetch 不会自动继承浏览器 Cookie。

成功响应先确认 200、NDJSON Content-Type 与非空 body，再通过 new Response(backendResponse.body) 原样转发，不先读取 text/json。X-Run-ID 仅在正整数字符串校验后传递；Set-Cookie 与内部头不透传。上游非成功响应取消正文，用状态白名单生成本地固定提示，保留 401/403 等分类，不读取可能含敏感信息的错误正文。

失败场景与取舍：页面门禁检查后，登录仍可能过期或撤销，聊天组件必须在进入 NDJSON 解析前处理非成功 HTTP 状态。当前 401 显示“账号与退出”重新登录指引并保留输入；不自动重发可能创建新 run 的请求。400/415/422 是输入问题，500 是服务故障。流内 RUN_ERROR 与流前 HTTP 错误属于两个处理阶段；当前没有额外给整段流套登录接口的短超时。

项目证据：src/app/api/chat/stream/route.ts、features/chat/chat-state.ts；test/features/auth/chat-stream-route.test.ts 新增 34 条验证 Cookie/来源/输入、状态脱敏与正文取消、未消费的流与分块、取消传播、run 头及异常流；聊天状态测试增加 5 条 HTTP 提示验证。2026-09-14 认证/BFF 136、聊天状态 76 条通过，TypeScript/ESLint 通过。

浏览器证据：test/browser/login-page.mjs 新增真实聊天与撤销令牌重放场景，整体 20/20 通过。请求经过真实页面、BFF 与 FastAPI；chat_test_app.py 仅模拟模型决策和 Redis 等待，其余认证、Agent Loop、流式服务与数据库存储沿用生产代码。401 时页面保留用户输入并提示重新登录；不据此宣称资源所有权已完成。

聊天所有权接入后，BFF 保留 404 分类，页面提示重新发送生成新会话；401 仍提示重新登录。身份有效不等于资源可访问。新增浏览器场景以真实两个账号和 BFF 验证甲会话被乙重放时 404、乙新标识成功，21/21 场景通过；前端单测认证/BFF 137、聊天状态 77 通过。
