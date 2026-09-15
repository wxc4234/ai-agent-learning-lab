# 服务端登录会话如何保存、过期与撤销？

> 复习优先级：高 | 关联项目：AI Agent Learning Lab | 参考答案已整理、尚未模拟

## 参考答案与设计取舍

SQLAlchemy Session 管理数据库工作单元，Conversation 保存聊天业务，LoginSession 保存登录凭证对应的服务端状态。用户 external_id 是业务身份，不能作为认证凭证。

签发服务生成高熵安全随机令牌，只将 SHA-256 摘要入库，原始令牌交给客户端。密码熵较低，需要 Argon2id 等慢哈希；足够随机的令牌可以使用快速摘要检索。摘要存储降低数据库泄露后直接重放令牌的风险，不能替代传输保护与日志脱敏。已完成存储、签发服务和 POST /auth/login 的 Cookie 签发；已实现只读登录令牌解析服务；已实现 GET /auth/me；已实现登出服务；已接入 POST /auth/logout；BFF 与浏览器登录链路尚未实现。

有效会话要求摘要匹配、created_at <= now、expires_at > now、revoked_at IS NULL。到期瞬间即失效；要求带时区时间并转换 UTC。数据库 CHECK 约束限制过期晚于创建、撤销不早于创建、摘要格式，唯一索引拒绝重复摘要。

撤销使用条件 UPDATE ... RETURNING：摘要匹配、已创建、尚未撤销时写入 revoked_at。重复调用返回 false，保留首次时间；已过期记录仍可标记撤销。单条条件更新减少先查再改的竞态窗口；当前测试验证顺序幂等，未做并发压测。

仓储只 add/flush，不 commit；由上层服务统一控制事务。flush 成功不代表最终提交成功。服务端存储便于集中撤销，代价是认证需要查询状态，后续引入缓存还需权衡一致性。

## 失败场景

- 撤销误用 expires_at > now：过期记录无法标记，重复撤销可能覆盖原时间。
- 有效查询使用 expires_at >= now：到期瞬间仍有效。
- 仓储自行提交：外层失败无法原子回滚。
- 只有 Python 校验：其他写入路径可以绕过，因此仍需数据库约束。
- 将存储当作完整认证：已完成 Cookie 属性和登录来源校验，但 BFF 转发、完整 CSRF 防护、鉴权和登出仍需落实。

## 项目证据

- `apps/api/app/models.py` 与 `apps/api/app/repositories/auth/login_session_repository.py`：关联用户、唯一摘要、时间约束、创建、查询、撤销。
- `apps/api/tests/auth/test_login_session_repository.py`：28 条 PostgreSQL 测试覆盖成功、失败、时间边界、时区以及真实提交/回滚。
- `apps/api/tests/migrations/test_login_session_migration.py`：隔离测试库演练升级、降级、再升级，与模型比较一致，原五张业务表逐行不变。降级删除登录会话表，仅在测试库演练。
- `apps/api/migrations/versions/c83f20a915bd_add_login_sessions.py`：新增登录会话表及索引。

2026-09-13：后端 335 条测试通过，Ruff 通过；本课模型、仓储、迁移和测试 Pyright 零错误、零警告。开发库升级至 c83f20a915bd，已有业务数据逐行不变。

## 签发事务如何编排？

复习优先级：高；参考答案已整理、尚未模拟。

服务先拒绝已有活动事务，再验证凭证、生成 32 字节安全随机令牌、计算 SHA-256 摘要、写入登录记录，commit 成功后才返回安全身份和 SecretStr 包装的令牌。默认有效期 8 小时，时间在认证成功后生成，可显式传入正的 timedelta。

SELECT 也会触发 SQLAlchemy autobegin，因此事务检查必须在认证之前。检查位于 try 外，拒绝调用方事务时不能顺手 rollback。认证函数只读且不管理事务；签发服务拥有本次事务，所以认证失败也由签发服务结束查询事务。仓储 flush 后若提交失败，不能将令牌当成功结果返回。

SecretStr 只保护默认显示，field(repr=False) 进一步省略结果里的 token；显式 get_secret_value 仍能取回明文。数据库错误保留分类，服务不记录凭证或异常详情，HTTP 边界已将未知异常映射为固定的通用错误。随机令牌唯一冲突不冒充密码错误，本课不增加自动重试。

失败场景与取舍：调用方先查用户再签发会被拒绝；仓储擅自提交会破坏统一回滚；在 commit 前返回会产生无持久化记录的凭证。网络在服务器提交后断开可能使客户端无法判断提交结果，rollback 不保证撤销服务器已完成的提交；当前故障注入覆盖提交前失败，未实现此类不确定结果的幂等恢复。

项目证据：`apps/api/app/services/auth/login_session_service.py` 和 `apps/api/tests/auth/test_login_session_service.py`。2026-09-14 新增 16 条 PostgreSQL 测试，覆盖独立物理连接可见性、真实摘要唯一冲突、提交失败后恢复、调用方 pending/flushed/read 三种事务状态，以及凭证错误、随机源故障、损坏哈希、有效期与脱敏。后端完整回归 351 passed，Ruff 通过；1 条 Starlette/AnyIO 弃用警告。未进行并发压测。

## Cookie 登录的 HTTP 边界如何设计？

复习优先级：高；参考答案已整理、尚未模拟。

题目：为什么登录成功只在 Set-Cookie 中交付令牌？HttpOnly 是否足以防 CSRF？输入错误为什么不能统一归为 500？

参考答案：POST /auth/login 在线程池中调用同步签发服务，每请求独立数据库 Session，提交成功后才设置 agent_session Cookie。JSON 只含 external_id、username。HttpOnly 限制脚本直接读取 Cookie，但浏览器仍可自动携带 Cookie 发请求；它不等于 CSRF 防护。Secure 默认 true，本地 HTTP 显式关闭；SameSite=Lax 提供部分跨站保护；Path=/，不设置 Domain，Expires 沿用数据库过期时间。服务端仍需在后续解析时检查过期和撤销。

本课要求 application/json，并将 Origin 与配置列表精确匹配；缺失、null、错误端口/协议或伪造域名后缀均拒绝。Origin 用于浏览器来源防护，不是用户认证，非浏览器客户端可自行设置。未来 BFF 必须校验并传递浏览器来源，不能无条件改写成可信 Origin，也不能把此处来源检查视为完整 CSRF/授权闭环。

局部 APIRoute 包装覆盖请求校验、执行和响应生成：校验错误返回安全 422，凭证错误返回统一 401，来源拒绝 403，非 JSON 415，解析层 HTTP 异常保留状态并隐藏详情，内部错误固定 500。成功和失败都 no-store；未知错误只记录 login_failed，无异常详情。若只在端点函数内 try/except，进入端点之前的输入校验可能遗漏。

失败场景：将 content-type 拼成 content_type 会令正常 JSON 请求全部 415；漏捕 RequestValidationError 会把客户端输入错误变成 500。登录失败不签发新 Cookie，也不等于清除了请求原有 Cookie。若 commit 已成功但 Cookie 交付失败，可能留下客户端未取得令牌的记录；本课未实现这种跨数据库/网络边界的补偿。

项目证据：apps/api/app/routers/auth/login.py、config.py、schemas.py、main.py 与 apps/api/tests/auth/test_login_api.py。2026-09-14 本课 29 条 PostgreSQL HTTP 测试通过，覆盖中文登录、Secure 开关、Cookie 摘要/Expires、Origin 边界、输入与解析错误、安全日志、提交前故障回滚、每请求 Session 关闭及线程池执行。测试未启动开发数据库 lifespan，尚未做浏览器 Cookie/BFF 联通验收。

本课最终回归：380 passed，Ruff 通过，1 条既有 Starlette/AnyIO 弃用警告。

## 只读身份解析为什么也需要事务边界？

复习优先级：高；参考答案已整理、尚未模拟。

题目：收到 Cookie 就能信任用户吗？为什么解析服务不能自行 rollback？no_autoflush 应覆盖哪些操作？

参考答案：原始令牌先经过格式检查，再计算摘要，查询有效且未撤销的会话，按其 user_id 查询用户，返回不含凭证的安全身份。Cookie 存在不代表数据库记录有效。当前令牌契约是 SecretStr | None，匹配签发器的 43 位 URL-safe 编码；不 trim、不改大小写。缺失、格式错误、未知、过期、撤销及不可用用户统一 InvalidLoginSessionError，数据库故障保持系统异常分类；无时区 now 是调用错误。

解析服务可能嵌入调用方工作单元，因此不提交、不回滚。SELECT 会开启事务，且默认可能触发 autoflush；必须用 no_autoflush 同时覆盖会话查询和用户查询，避免把调用方未准备提交的数据提前发给数据库。no_autoflush 不是无事务，也不会撤销调用方已经 flush 的写入。发生真实 PostgreSQL 查询错误时，调用方负责 rollback 后恢复 Session。

失败场景与取舍：只保护第一次查询会令第二次查询触发写入；将数据库异常统一转成登录失效会掩盖服务故障；把 external_id 当凭证允许客户端冒用身份。读取身份只代表本次查询所见状态，不能宣称实现了并发撤销与业务执行的原子隔离或跨请求缓存一致性。

项目证据：apps/api/app/services/auth/login_session_resolver.py、repositories/user_repository.py 与 tests/auth/test_login_session_resolver.py。新增 29 条 PostgreSQL 测试覆盖真实签发后解析中文身份、格式与错误脱敏、创建/过期端点、时区、撤销、用户异常、两次查询的 autoflush 防护、调用方事务保留，以及真实数据库故障由调用方回滚恢复。受数据库外键/CHECK 约束禁止的用户异常状态通过仓储返回值注入验证，不宣称这些状态已真实写入数据库。

## 当前用户接口如何防止身份冒用并释放资源？

复习优先级：高；参考答案已整理、尚未模拟。

GET /auth/me 只从 agent_session Cookie 获取令牌并包装成 SecretStr，查询成功只输出 external_id、username。客户端传来的查询参数、请求体或 X-User-Id 不能替代令牌身份。此 GET 不要求登录 POST 的 JSON/Origin 条件，不续期、不清 Cookie；也不新增跨源读取许可。401 表示登录无效，500 表示内部故障，两类不能混用。

当前 HTTP 依赖每请求创建独立 Session，同步解析在线程池执行。退出 with SessionLocal() 会关闭数据库 Session，结束未提交事务并释放连接资源，这不等于撤销 LoginSession。响应生成也纳入局部 APIRoute 错误边界；成功和失败都 no-store，JSON 与日志不包含令牌，也不产生 Set-Cookie。

项目证据：apps/api/app/routers/auth/current_user.py、config.py 的共享 Cookie 名称、schemas.py、main.py，以及 tests/auth/test_current_user_api.py。新增 16 条测试覆盖真实 HTTP 登录到当前用户查询、旧 Cookie 遭数据库过期/撤销拒绝、身份字段冒用无效、真实查询故障恢复、响应校验故障脱敏、SecretStr 包装、线程池与 Session 关闭。仅验收后端 TestClient 链路，不宣称浏览器/BFF 登录闭环完成。

## 登出为什么要撤销服务端记录并支持幂等？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：清除浏览器 Cookie 不能使其他地方保存的令牌失效。logout_user 以摘要调用条件 UPDATE，仅撤销本次令牌对应记录，commit 成功后才返回实际是否撤销。重复调用保持首次 revoked_at 不变；首次返回 true、之后 false 仍满足状态幂等。缺失/格式错误不查询，未知令牌执行更新但无匹配时仍结束事务；系统错误不能吞成幂等成功。

服务拥有事务，检查已有事务位于 try 外且优先于令牌校验，避免连缺失令牌路径也误干预调用方工作。失败回滚后保留异常分类。不能先调用有效会话解析器，因为过期令牌仍应允许标记撤销。单设备登出以令牌摘要定位，不按 user_id 撤销全部会话。

项目证据：apps/api/app/services/auth/logout_service.py 与 tests/auth/test_logout_service.py。新增 26 条 PostgreSQL 测试覆盖真实签发→登出→解析失败、其他令牌有效、独立物理连接可见、首次撤销时间保持、过期/未来记录、无效输入、调用方 pending/flushed/read 事务、提交前故障及更新后真实 SQL 失败回滚、Session 恢复与脱敏。未做并发撤销压测或服务器已提交后网络断开的不确定结果恢复。

## HTTP 登出如何同时完成服务端撤销与客户端清 Cookie？

复习优先级：高；参考答案已整理、尚未模拟。

POST /auth/logout 先精确校验 Origin，再从 Cookie 提取 SecretStr 令牌，等待登出服务结束事务后发送 204 和立即过期的 Set-Cookie。缺失、错误、未知、已撤销令牌都允许幂等成功，不前置要求有效登录；服务 false 与 true 均清 Cookie，数据库异常则 500 且不发送清 Cookie 头。接口无请求体契约，不要求 Content-Type；Origin 防护不能遗漏。

删除 Cookie 必须对应原名称、Path=/ 和不设置 Domain，并沿用 Secure/HttpOnly/SameSite 策略。成功与失败都 no-store；204 无正文。只观察浏览器 Cookie 消失无法证明撤销完成，还必须重放旧令牌并确认 /auth/me 返回 401。数据库提交与浏览器接收清 Cookie 不是跨系统原子操作；若提交成功但响应丢失，可重试幂等登出，不宣称回滚已提交的撤销。

项目证据：apps/api/app/routers/auth/logout.py、schemas.py、main.py 与 tests/auth/test_logout_api.py。新增 18 条 PostgreSQL HTTP 测试覆盖登录→查询→登出→旧令牌重放失败、其他会话仍有效、重复/无效/过期 Cookie、删除属性与 Secure 开关、Origin 拒绝、提交失败回滚且保留 Cookie、脱敏日志、线程池与 Session 关闭、204/OpenAPI 及旧接口回归。仅完成后端 TestClient 链路，尚未做 BFF/浏览器 Cookie 验证。


## FastAPI 当前用户依赖如何管理身份、线程和数据库生命周期？

复习优先级：高；参考答案已整理、尚未模拟。

CurrentUser = Annotated[AuthenticatedUser, Depends(require_current_user)] 是依赖类型别名，不是共享的用户实例。FastAPI 根据每次请求的 Cookie 调用同步依赖；一个请求内同一依赖默认缓存结果，多次声明复用一次执行，不同请求重新解析。客户端 external_id/username 参数不提供身份值。普通 def 依赖由框架在线程池调度，即使使用它的是 async 路由；自行在 async 函数里直接调用普通同步函数不具备这种自动调度。

依赖在 with SessionLocal() 内调用已有解析服务，离开 with 后返回不可变安全数据对象。Session 在接口函数执行前已经关闭，后续异步路由或 StreamingResponse 生成器不持有认证查询连接；不会把 ORM 对象、密码或原始令牌交给接口。需要数据库的后续业务操作应管理自己的 Session/事务，不复用已关闭的认证 Session。

依赖失败时接口函数不执行。依赖保留 InvalidLoginSessionError 与数据库故障分类；当前 /auth/me 的 CurrentUserRoute 捕获依赖求解阶段的异常，返回既有安全 401/500 和 no-store。此错误包装尚未全局复用，其他路由添加 Depends 时也必须设计相应错误处理。认证成功不等于资源所有权校验成功。

项目证据：app/dependencies.py、routers/current_user.py；test_current_user_dependency.py 新增 9 条测试，验证异步接口中依赖线程与 Session 提前关闭、流式正文执行前关闭、请求内缓存/请求间隔离、身份不能冒用、无效凭证不运行接口、真实 SQL 故障及工厂异常安全处理、OpenAPI 不暴露身份输入。原有 current_user/logout HTTP 测试的替换位置迁至 dependencies，真实解析与数据库仍被覆盖。2026-09-14 全后端 478 passed、零弃用警告，Ruff 通过。

## 流式聊天应该在什么时候认证？认证成功是否就能访问任意会话？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：在创建 Agent Run、调用模型及发送响应头之前，通过路由依赖解析 Cookie。普通聊天与流式聊天必须分别声明依赖，遗漏任意入口都会留下绕过路径。同步认证依赖在线程池查询数据库，返回安全身份前关闭 Session，避免流式连接长期占用认证连接。

ChatRoute 包装依赖求解和路由执行：状态变更请求先验证精确 Origin 与 JSON 类型；无效登录为 401，数据库故障为脱敏 500，输入错误为 422，模型入口错误为 502，均 no-store。流开始后不能再修改 HTTP 状态码，后续故障由既有 RUN_ERROR 协议表达。BFF 的 Cookie 格式检查只是提前拒绝明显无效输入，服务端仍须查验摘要、有效期和撤销状态。

失败场景：只保护普通 /chat 时，/chat/stream 仍创建 run；只做首页门禁时，手工 HTTP 请求可绕过页面；把数据库故障当成 401 会诱导用户反复登录；先创建 run 再认证会留下无效记录与副作用。项目首次验收确实以三个失败测试复现流式装饰器遗漏，学习者补齐后通过。

取舍：本课完成身份验证，尚未实现资源所有权。现有 conversation 仓储和内存缓存仍按 session_id 工作，历史/运行查询与取消入口尚未整体接入授权。下一步必须把服务端身份贯穿持久化、缓存、查询和取消，防止已登录用户跨账号访问。

项目证据：routers/chat.py、routers/chat_boundary.py、tests/chat/test_chat_auth_boundary.py（14 条）、tests/chat/test_chat_auth_sessions.py（18 条）。后者复用真实隔离 PostgreSQL，验证有效/缺失/未知/错误格式/过期/撤销凭证、Session 在业务与流前关闭、SQL 故障与输入脱敏；业务模型与 run 创建模拟并断言拒绝时不调用。2026-09-14 后端全量 510 passed、零弃用警告。

## 会话创建如何避免并发接管，并保留事务边界？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：身份认证之后仍须校验资源归属。require_owned_conversation 同时使用 external_id 和 user_id 查询；不存在与非本人均返回相同业务错误，不回显其他用户信息。当前 external_id 全局唯一，get_or_create_owned_conversation 用 PostgreSQL INSERT ON CONFLICT DO NOTHING 处理该唯一键争用，再按 owner 查询；不使用 DO UPDATE 改写已有 user_id。

并发场景：两个事务争用未提交的相同唯一键，后者等待前者结束。在当前 READ COMMITTED 下，前者提交后，后者跳过插入，后续 SELECT 获取新快照；同一用户复用记录，不同用户被拒绝。前者回滚后，等待者可插入属于自己的记录。唯一约束与所有权查询缺一不可；只做“先查后插”不能消除竞争。更高隔离级别可能产生序列化错误，需要调用方事务重试，不宣称当前原语自动处理。

事务取舍：仓储接受调用方 Session，执行 SQL 但不 commit。调用方可把会话与 run 创建放在同一事务中，后续失败一起回滚。ORM Session 默认 autoflush 仍可能刷新调用方 pending 数据，这不等于提交；本函数不是禁止 autoflush 的认证只读服务。冲突处理仅指定 external_id，外键错误和其他唯一约束冲突保留数据库异常，数据库事务失败后由调用方 rollback。

项目证据：conversation_repository.py 与 test_owned_conversation_repository.py（15 条）。测试通过独立连接观察未提交记录不可见，确认提交/回滚行为；pg_blocking_pids 证明实际锁等待而非顺序调用。全后端 525 passed、零弃用警告。现有聊天读写与内存缓存尚未接入这些原语，不把仓储通过等同于系统已完成用户隔离。

## 如何把所有权校验贯穿模型上下文，而不被缓存绕过？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：HTTP 通过 CurrentUser.id 获取可信身份，再显式传给会话准备、历史读取、完整轮次保存和 run 创建。请求体中的 user_id 不参与身份决定。流式路由在返回 StreamingResponse 前，由 create_agent_run 在事务中取得属于当前用户的会话，再创建运行；越权时返回脱敏 404，不生成 run 或事件、不调用模型。普通聊天在准备上下文时先检查归属。

缓存键使用 (user_id, session_id)，并在每次命中前重新执行数据库所有权检查。仅更换缓存键仍不够：过时或误写的缓存不能成为授权依据。读取历史成功后才发布缓存；数据库失败不能留下半成品。历史查询同样要求 CurrentUser，自己的空会话为 200/空列表，不存在或非本人统一 404；保存消息也按所有权定位记录。

取舍和边界：会话 external_id 仍全局唯一，不接管 local-demo-user 的历史数据。普通聊天会先提交空会话准备，流式聊天将会话与 run 创建放在同一事务；模型调用不长期占用该事务。此节记录聊天与会话历史课的范围。运行时间线与取消的后续实现见 [运行所有权与终态互斥](agent-run-observability.md)；同一用户同一会话并发写入的顺序与缓存一致性仍未解决。

项目证据：chat.py、conversation.py、chat_boundary.py、chat_service.py 和两个仓储；test_chat_ownership.py 新增 12 条真实 Cookie/隔离 PostgreSQL 测试，仅模拟模型和 Redis 等待。验证双用户普通/流式聊天、模型收到的上下文、数据库记录数量、缓存预置不绕过、清缓存后从持久化恢复、历史状态码、跨用户保存拒绝、匿名历史不可接管与故障脱敏。

## 为什么模型、实际数据库结构和 Alembic 版本记录必须分别核对？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：ORM 模型描述期望结构，数据库目录记录真实表结构，alembic_version 记录已经执行到哪一步；三者可能因 create_all、人工操作或环境切换而不一致。已有表并不能证明迁移版本正确，版本落后也不代表迁移涉及的表一定不存在。直接 upgrade 可能撞到重复表，直接 stamp 则可能掩盖缺失结构。

项目证据：Workspace 课预检发现开发库版本为 b62d19f804ae，但 login_sessions 已存在。先以 compare_metadata（含 server default）检查除新 Workspace 外的结构，再单独核对登录会话的 CHECK 约束，与 c83f20a915bd 目标一致。隔离 PostgreSQL 演练通过后，在单事务内锁住迁移版本表、重新检查前置条件，校准已存在结构对应的版本，执行新增 Workspace 表的迁移并更新最终版本。最终为 d94e31b706fa，alembic check 无差异。

取舍与失败场景：结构存在的原因未追溯，不能断言一定由 create_all 造成；校准只适用于已证实全部迁移效果已存在的这一次情形，不把 stamp 当作通用恢复命令。若存在数据回填等非结构操作，仅比对表结构还不够。这里旧登录迁移只有建表/索引，不含数据转换；没有重写旧迁移、删表或重建业务数据。迁移升级/回退及旧表逐行保持在隔离库验证，开发库未做业务数据逐行读取。

配套证据：test_workspace_repository.py（19 条）与 test_workspace_migration.py（2 条）；覆盖所有权、UUID/名称、flush 与 commit 的跨连接可见性、调用方回滚、约束失败、完整旧迁移链、版本校准与目标模型比较。

## 服务为什么先提取结果、提交成功后再返回？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：拥有事务的服务在提交前将 ORM 字段复制到不可变普通结果对象，提交成功后才返回。这样既不把未提交结果当成功，也避免默认 expire_on_commit=True 时提交后读取 ORM 属性触发隐式查询和新事务。结果仅包含对外标识、规范化名称和数据库创建时间，不暴露 ORM 对象或内部用户字段。

事务检查必须在 try/rollback 边界之外：传入 Session 已有显式、只读、pending 或 flushed 事务时，服务拒绝接手，也不能顺手回滚调用方工作。仓储只 flush；服务管理 commit/rollback，调用方负责关闭 Session。名称校验异常与数据库异常保留分类，HTTP 脱敏留给后续路由边界。

Workspace 项目证据：workspace_service.py 与 test_workspace_service.py 的 20 条隔离 PostgreSQL 测试。覆盖两种 expire_on_commit 配置、结果关闭 Session 后可用、冻结与字段白名单、既有事务保护、真实外键错误/SQL 错误、提交前注入故障回滚已 flush 记录、结果构造失败及 Session 恢复。提交故障测试模拟的是提交前失败，不宣称处理了服务器已提交但客户端未收到确认的分布式不确定性。

Workspace HTTP 补充证据：真实 app 的测试先发现遗漏 include_router 导致 404，说明仅把 router 挂到测试专用 app 无法覆盖应用装配。补齐注册后，29 条 HTTP 测试验证真实 Cookie 归属、输入身份字段拒绝、Session 关闭和错误脱敏；与创建服务 20 条、认证依赖 9 条组成 58 条定向回归，未声称执行全量。

失败边界：服务提交之后仍可能发生 HTTP 响应构造或序列化错误。测试注入无效响应字段，确认返回安全 500、数据库记录仍为 1。事务只能覆盖提交之前的工作；客户端失败响应不必然表示业务没成功，后续幂等设计应解决重复提交，而不能依赖“所有 500 都回滚”的假设。


BFF/页面补充证据（2026-09-15）：Workspace 创建响应白名单与状态/错误码联合校验，唯一合法 Cookie 转发，取消和超时覆盖 fetch 与正文读取，错误不透传内部信息。注册页面区分“账号创建成功”和“登录会话已建立”，创建请求超时后不自动重试；结果未知时允许尝试登录确认。119 条 Workspace/注册/原登录 BFF 定向测试与两个隔离浏览器场景验证这些边界，真实新账号可登录并创建自己的 Workspace。参考答案已整理、尚未模拟。


## 本地 Coding Agent 免登录后，为什么仍保留身份和访问边界？

复习优先级：高；参考答案已整理、尚未模拟。

参考答案：免产品登录是交互方式变化，不等于删除资源归属。固定本机主体复用 Workspace/会话/Run 外键，避免修改所有业务仓储；数据库唯一约束与 ON CONFLICT 处理并发首次初始化。原账号资源不自动迁给本机用户。单安装实例的多个浏览器共享一个本机身份，因此不能将其当作多租户云服务。

浏览器只访问回环地址 BFF，内部随机凭证仅在服务端配置与 BFF/API 请求间传递；BFF 拒绝错误来源、跨站与远程上游，API 再检查 Host/Origin/凭证。操作文件仍要 Workspace 路径范围与执行审批；本机内部凭证不是 Sandbox，也不能直接作为公网认证方案。模型 API Key 与本机内部凭证用途不同，调用云模型时选入上下文的数据仍会离开电脑。

项目证据：test_local_mode.py 覆盖稳定主体、并发初始化、不接管账号数据、无效凭证/Host/Origin 拒绝、配置失败关闭和事务保护；local-mode.test.ts 检查 BFF 只注入服务端凭证、无 Cookie 聊天/取消/创建、禁止远程上游及账号变更。主线切换由教练按用户授权直接实现，不能据此声称学习者已独立掌握。
