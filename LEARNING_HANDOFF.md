# AI Agent 学习交接

更新时间：2026-09-14（Asia/Shanghai）

本文件是新学习会话唯一必须主动读取的动态进度入口。长期仓库与教学规则由自动加载的 [AGENTS.md](AGENTS.md) 提供；完整路线、大纲、架构和历史材料只按当前任务读取相关章节，不在启动时整篇加载。

## 当前学习快照

- 目标岗位：**AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**。
- 主项目：可观测、可恢复、可安全执行代码任务的 Codex-like Coding Agent。
- 正式周进度：第 1～3 周完成，**3 / 12（25%）**；当前在提前推进第 4 周认证基础，不把部分预完成记作整周完成。
- 学习方式：一次只推进一个可运行、可验证的小任务；教练先按“是什么 → 为什么需要 → 怎么使用 → 项目示例”讲解并给完整核心参考实现，学习者亲手实现核心逻辑。
- 验收时机：只有学习者明确说本课“完成了”后，教练才检查实际 Diff、创建测试与机械性配套、运行验证并更新进度。
- 工作目录：后续学习使用已检出 `main` 的主仓库；每次开始仍须检查实际分支和工作区，不覆盖已有用户改动。

## 已验收基线

### 第 1～3 周主链路

- FastAPI + DeepSeek、PostgreSQL 持久化会话、Redis、Alembic 与 Docker Compose 已接通。
- Next.js BFF 原样转发 FastAPI NDJSON 流；浏览器可处理任意网络分块、工具事件、停止、超时、重试和六态运行状态。
- Tool Registry、Pydantic 参数校验、顺序 Agent Loop、结构化 Observation、执行超时、取消传播和最大步骤保护已完成。
- `run_id`、Agent Run/Event 时间线、Token usage、模型/工具耗时、人民币费用估算和 Token 续跑预算已完成。
- Redis Pub/Sub 已用于跨实例取消；Redis 幂等、限流和短期状态仍未实现。
- 第 3 周复盘见 [week-learning/week-03/REVIEW.md](week-learning/week-03/REVIEW.md)，事件与架构细节见 [docs/architecture.md](docs/architecture.md) 和 [docs/agent-ui-events.md](docs/agent-ui-events.md)。

### 第 4 周已预完成的认证基础

- `User` 已支持成对出现的可空 `username` / `password_hash`；历史无凭证用户继续兼容。
- Alembic 已包含用户凭证与会话唯一索引兼容修复，以及登录会话迁移 `c83f20a915bd`；当前 head 为 `c83f20a915bd`。
- 密码服务使用 Argon2id；正确密码、错误密码、随机盐、Unicode、空白和损坏哈希行为已验收。
- `RegisterRequest` 已完成：用户名去首尾空白、允许 3～64 个汉字（Unicode 统一汉字含扩展及〇）、ASCII 字母/数字/下划线，英文转小写；注册与登录共用规范，拒绝内部空白、emoji、标点、全角字母，不做 NFKC 转换；密码为 8～128 字符、禁止所有 `str.isspace` 空白、不自动 trim，并用 `SecretStr` 承载。
- `create_registered_user` 已完成：生成 UUID4 `external_id`，只 `add/flush`，不 `commit`、不吞 `IntegrityError`；`get_user_by_username` 已存在。
- `register_user` 注册服务已验收：服务拥有事务，拒绝已有活动事务，成功提交、失败回滚；仅将 PostgreSQL `users.ix_users_username` 的唯一冲突转为业务错误，其他异常保留分类。返回身份结果，不包含凭证；业务冲突使用 `from None` 抑制默认异常链展示。
- `POST /auth/register` 已通过 HTTP 验收：201 安全身份结果，409 用户名冲突，422 输入校验失败，400 解析失败，500 通用内部故障；局部 APIRoute 包装校验与执行，错误 JSON 为对象，日志不记录异常详情。同步路由在线程池执行，每次独立 Session 并关闭。
- `LoginRequest` 与 `authenticate_user` 已验收：密码原样保留 1～128 字符；未知用户名/无凭证/密码错误统一业务错误，未知用户执行一次虚拟哈希验证，哈希损坏与数据库故障保留分类。只读、不 autoflush、不提交或回滚调用方写入，返回安全身份。
- 登录会话签发服务 `issue_login_session` 已验收：复用凭证验证、32 字节安全随机令牌、SHA-256 摘要入库，默认有效期 8 小时，提交成功才返回 `SecretStr` 令牌及安全身份；拒绝调用方已有事务，失败回滚且保留异常分类。
- `POST /auth/login` 已验收：JSON + 精确 Origin 校验，提交后签发 HttpOnly/SameSite=Lax/Path=/ Cookie，Expires 沿用数据库到期时间，不设置 Domain；Secure 默认 true，本地 HTTP 配置 false。正文只含安全身份，错误统一脱敏，成功/失败 no-store，每请求独立 Session 并在线程池执行。已完成只读令牌解析服务；已接入 GET /auth/me；已完成登出服务；已完成 POST /auth/logout；登录与当前用户 BFF 已验收，登录/身份查询/登出 BFF 与独立登录页均已验收。
- `LoginSession` 与仓储已验收：只保存唯一的 64 位小写十六进制令牌摘要，关联用户，使用带时区时间；有效区间为创建时间含端点、过期时间不含端点。撤销只更新尚未撤销的记录，重复调用不覆盖时间，允许撤销过期记录；仓储只 flush，不提交调用方事务。

### 最近验证结果

- 后端签发、解析、登录/身份查询/登出服务及 HTTP 专项已验收；历史测试细节见 apps/api/tests 与 ENVIRONMENT.md。最新后端基线 537 passed、零警告，Ruff 通过。
- 数据库测试统一 PostgreSQL + psycopg；conftest 每轮创建随机独立测试库、每例私有 schema，支持真实 commit/rollback 并自动清理。不用 SQLite、不连接开发业务表、不使用旧 RUN_POSTGRES_* 开关。运行方式见 ENVIRONMENT.md。
- 本机开发数据库 head 为 `c83f20a915bd`，上一轮迁移 check 无差异，原五张业务表升级前后逐行一致；后续服务/接口课程未新增迁移。
- 项目 `.venv` 已正式升级为 Python 3.12.13，`.python-version` 固定 3.12，旧环境保存在 Git 忽略的 `venv/python310-backup-20260914/`。requirements 固定 AnyIO 4.14.2，规避 Starlette 1.6.0 TestClient 旧 BlockingPortal 别名警告；未屏蔽警告或修改第三方源码，待 Starlette 正式修复后评估升级。pip check 已通过。
- 本地 `.env` 的 LOGIN_COOKIE_SECURE=false 仅用于 HTTP，生产 HTTPS 必须 true。配置/依赖变更后重启已有进程；Docker 已启动。
- 前端最新基线为认证/BFF 137 passed、聊天 77 passed，TypeScript/ESLint 通过；本课未重复 Pyright。2026-09-12 Windows 环境曾验收通过，跨电脑更新仍须各自安装 requirements 和执行迁移检查。



- 当前用户 BFF、登录页与页面异步状态已验收；细节、隔离浏览器命令及历史结果见 ENVIRONMENT.md 和现有测试。

- 用户已授权教练直接完成 UI 配套替换，不安排新课、不改变下一课：登录页、聊天输入和运行摘要改用 shadcn/ui 的 Button/Input/Textarea/Label/Card，统一语义主题并随系统切换明暗；共享控件在 apps/web/src/components/ui，后续优先复用。核心认证和聊天状态逻辑保持原样。依赖与许可证见 ENVIRONMENT.md 和 apps/web/THIRD_PARTY_NOTICES.md。UI 验收：认证 102、聊天 71、浏览器 11 场景通过，TypeScript/ESLint 通过；明暗截图已检查，临时资源已清理。

- 首页门禁、登录返回与当前用户依赖均已验收；Session 生命周期、请求内依赖缓存及页面异步竞态证据见既有测试和 ENVIRONMENT.md。

## 最近完成：聊天认证与会话所有权闭环

- 两个聊天入口使用 CurrentUser.id 传递可信身份；流式路由在发送响应头前，在事务中确认会话所有权并创建 run。ChatRoute 将未登录映射为 401、无权访问会话映射为统一 404，错误脱敏/no-store；POST 保留精确 Origin/JSON 检查。
- 会话仓储 require_owned_conversation 同时限制 external_id/user_id；get_or_create_owned_conversation 使用 PostgreSQL ON CONFLICT DO NOTHING，仅针对全局唯一 external_id，不接管他人记录。15 条仓储测试验证事务与真实并发锁等待（READ COMMITTED），更高隔离级别重试未实现。
- load_conversation/save_conversation_turn 现必须传入 user_id；save 只访问已有的本人会话。ensure_owned_conversation 管理普通聊天准备事务。旧匿名创建函数/常量/导入已清理，匿名历史保留，不迁移。缓存键为 (user_id, session_id)，命中前仍查归属，历史读取成功才发布缓存。
- GET /sessions/{session_id}/messages 已接入身份/所有权；自己的空会话返回 200/空列表，未知或非本人统一 404。用户指出 Pydantic 构造器静态类型报错后，教练按明确授权补 ConversationMessage.model_validate 显式转换；该文件 Pyright 0 errors/0 warnings，专项 12 条复跑通过。
- BFF 只转发有效唯一 Cookie 与 Origin，成功流保持 body/取消信号，404 安全映射；页面区分 401 与 404，保留输入，提示重新发送以生成新标识。当前发送生成新 session_id，重试沿用旧标识。
- 最新验收：后端全量 537 passed、零弃用警告；前端认证/BFF 137、聊天状态 77、浏览器 21 场景通过；Ruff/TypeScript/ESLint 通过。新增 test_chat_ownership.py 12 条真实 Cookie/隔离 PostgreSQL 测试，覆盖双用户、模型上下文/持久化、缓存预置不绕过、清缓存恢复、历史状态、跨用户保存拒绝、旧匿名不可接管与故障脱敏。旧流式/Run 测试已适配 user_id；模型和 Redis 等待模拟。
- 浏览器测试复用 run-isolated.py/login-page.mjs/chat_test_app.py；新增第二账号与真实 BFF 双用户场景：甲创建→切换乙沿用甲标识 404→乙新会话成功。全 21 场景通过，临时服务与隔离数据库已清理，无真实模型调用。重要面试题及索引已更新，参考答案已整理、尚未模拟。

## 唯一下一课

**第 4 周：运行时间线与取消接口的所有权校验。**

聊天读写、历史查询和缓存已接入会话所有权，正式周进度仍为 3 / 12。接下来保护 GET /runs/{run_id} 与 POST /runs/{run_id}/cancel：根据运行关联的 Conversation.user_id 限制访问，用户身份只来自 CurrentUser。取消的授权应在写入取消状态/事件及发布 Redis 通知之前完成，并保留幂等与既有终态规则。

先梳理 runs.py、run_repository.py、run_cancellation.py、前端取消 BFF 与 requestCancellation，给出一个可运行闭环：认证 Cookie/Origin 转发、401/404/服务错误、已结束运行与重复取消、跨用户拒绝不写数据库/不发布通知。不要只保护后端而导致现有停止按钮失效。按需读取 skills/agent-streaming/SKILL.md，继续模型模拟与 PostgreSQL 隔离测试；先给完整核心参考，学习者完成后再补测试。

未完成边界：运行时间线和取消入口当前仍未授权，不能宣称应用已有完整多用户隔离。同一用户同一会话的并发排序、普通聊天非模型异常/取消后的缓存一致性仍沿用既有行为，未作为本课新增能力；不得宣称这些已解决。历史匿名数据不擅自迁移，不展开 Workspace/Task。

## 保留但不阻塞当前课程的问题

- Chrome 152 已验证发送前离线、旧摘要清除、恢复后真实重试、模拟 HTTP 429 及移除模拟后的真实重试。
- “文本出现后、终态前断网”尚未确认。当前模型适配器使用 `stream=False`，完整答案一次进入文本事件，不能描述为逐 Token 输出。
- 详细记录与剩余验收见 [docs/chat-browser-fault-validation.md](docs/chat-browser-fault-validation.md)。该项继续保留，但不阻塞认证学习。

## 新会话恢复规则

1. 使用自动加载的 `AGENTS.md`，主动读取本文件。
2. 检查 `git status --short --branch`、当前分支和“唯一下一课”列出的相关代码；有用户改动时先保护并理解改动。
3. 正式授课需要路线细节时，只读取 `LEARNING_CURRICULUM.md` 的第 4 周当前小任务；不要整篇读取课程大纲。
4. 只有调整整体路线时读取 `LEARNING_PLAN.md`，涉及最终产品能力取舍时读取 `docs/codex-like-agent-scope.md`，需要核对教学细则时读取 `LEARNING_COACH_GUIDE.md` 对应章节。
5. FastAPI、Next.js BFF、流式渲染或 Agent 状态相关变更读取 `skills/agent-streaming/SKILL.md`；工具或 Agent Loop 变更读取 `skills/agent-runtime/SKILL.md`。未触发时不预加载。
6. 得到当前目标、约束、相关代码和验收标准后立即开始本课，不为“可能有用”继续扩展上下文。

不得从第 1 周重讲，也不得因基础 Runtime 已完成就跳到第 7 周。当前课程接续始终以本文件“唯一下一课”为准。

## 按需文档索引

| 需要的信息 | 读取位置 |
|---|---|
| 12 周整体路线与技术栈 | [LEARNING_PLAN.md](LEARNING_PLAN.md) |
| 当前周逐日任务与验收标准 | [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md) 对应周章节 |
| 逐课教学、验收与纠错细则 | [LEARNING_COACH_GUIDE.md](LEARNING_COACH_GUIDE.md) 对应章节 |
| Codex-like 最终能力边界 | [docs/codex-like-agent-scope.md](docs/codex-like-agent-scope.md) |
| 环境安装、迁移与启动 | [ENVIRONMENT.md](ENVIRONMENT.md) 对应操作系统章节 |
| 已结束周次的学习记录 | [week-learning/README.md](week-learning/README.md) |
| 面试题与项目证据 | [interview-questions/README.md](interview-questions/README.md) |

## 更新约束

每课完成后只更新：当前学习快照、已验收基线、唯一下一课和未解决问题。详细过程应进入相关代码、测试、架构文档、面试题或周复盘，不在本文件重复堆叠。

本文件建议保持在 8,000 字符以内；超过时优先删除已被代码、测试或其他文档承载的过程描述，不能删除仍会影响下一课的决策、风险和验收状态。
