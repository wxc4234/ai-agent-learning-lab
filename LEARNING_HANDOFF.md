# AI Agent 学习交接

更新时间：2026-09-13（Asia/Shanghai）

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
- 尚无登录 HTTP、登出或 Cookie 会话；凭证验证通过不等于建立登录态。
- `LoginSession` 与仓储已验收：只保存唯一的 64 位小写十六进制令牌摘要，关联用户，使用带时区时间；有效区间为创建时间含端点、过期时间不含端点。撤销只更新尚未撤销的记录，重复调用不覆盖时间，允许撤销过期记录；仓储只 flush，不提交调用方事务。

### 最近验证结果

- 2026-09-13 后端数据库测试已统一 PostgreSQL + psycopg：默认运行 `335 passed`，无数据库测试跳过项；Ruff 通过。注册服务 10 条测试覆盖成功提交、事务所有权、失败回滚、真实约束分类、Session 恢复与回溯脱敏。
- `tests/conftest.py` 自动创建随机独立 PostgreSQL 测试库，每例独立 schema，允许真实提交并自动清理；不连接开发业务表、不使用 SQLite 或 `.test-tmp-*`。通过 `ENVIRONMENT.md` 中的普通 pytest 命令运行，不再使用两个旧的 `RUN_POSTGRES_*` 开关。
- 本课新增 51 条输入模型测试、15 条凭证服务测试（含中文注册 HTTP → 凭证校验联通）；schemas、authentication_service 及两个新测试文件 Pyright 零错误、零警告。Docker 曾停止导致首次测试中断，恢复现有容器后完整重跑通过。
- 注册 HTTP 新增 21 条测试，验证真实持久化、冲突/输入/内部错误安全响应、故障恢复、日志脱敏、Session 关闭、线程执行及既有聊天校验行为。
- 新增 2 条隔离测试验证测试库/私有 schema 及不同物理 PostgreSQL 连接间的真实 commit/rollback 可见性，不再以保存点替代注册服务提交；不宣称已做并发压测。数据库测试连接要求见 `ENVIRONMENT.md`。
- 前端：`71 passed`，TypeScript 类型检查和 ESLint 通过。
- 登录会话新增 29 条 PostgreSQL 测试，覆盖时间边界、约束、幂等撤销、真实提交/回滚及迁移升降级；模型、仓储、迁移及两个测试文件 Pyright 零错误、零警告。测试结束无遗留临时测试库。
- 本机 PostgreSQL 已升级至 `c83f20a915bd (head)`，`alembic check` 返回 `No new upgrade operations detected.`。升级前后原五张业务表逐行比较一致（用户 1、聊天 3、消息 4、运行 3、事件 46）。
- 2026-09-12 已在 Windows 验证 Python 3.12、`pnpm 10.34.1`、PostgreSQL/pgvector 与 Redis 环境；现有 `.env` 和命名卷数据未被覆盖或删除。

测试数量只用于确认当前基线；新增课程后应以实际测试输出为准，不因数字变化误判回归。

## 唯一下一课

**第 4 周：登录会话签发服务与事务编排。**

登录会话模型与持久化已验收（2026-09-13），学习者已修正撤销条件。测试、迁移和重要面试题已补齐，参考答案已整理、尚未模拟。正式进度仍为 3 / 12；不要重复注册、凭证验证或会话存储课程。

下一课目标：组合凭证验证、安全随机令牌生成、SHA-256 摘要入库与事务提交；提交成功后才返回受保护的原始令牌及安全身份信息。一次只实现签发服务，不同时接入 HTTP、Cookie、前端或授权体系。

开始前按需读取：

- `apps/api/app/models.py`、`apps/api/app/database.py`
- `apps/api/app/services/authentication_service.py`
- `apps/api/app/repositories/login_session_repository.py`
- `apps/api/app/services/registration_service.py` 的事务所有权模式
- `apps/api/tests/conftest.py`、`apps/api/tests/test_authentication_service.py`、`apps/api/tests/test_login_session_repository.py`

必须保持的设计边界：

- 区分数据库 Session、聊天 Conversation 和登录会话；已有 external_id 是业务身份，不是认证凭证。
- 会话凭证必须使用密码学安全随机值，数据库只存令牌摘要并关联用户，明确过期时间、撤销方式及事务所有权；不能把密码或随机令牌明文写进日志。
- 保留中文用户名规范和已有历史用户；不得通过会话迁移重建或清空开发数据。
- 注册、凭证验证、会话签发分层；Cookie 的 HttpOnly/Secure/SameSite、BFF 转发、CSRF 与登录/登出接口在接入时分别落实，不能把存储模型视为完整登录闭环。
- PostgreSQL 测试使用独立测试库；只有学习者明确说“完成了”后才补齐迁移/测试等配套并验收。

验收范围在下一课开场落实：至少覆盖成功签发且数据库无明文令牌、中文用户名、错误凭证不写入、过期时间、随机令牌不重复、真实提交/失败回滚及事务所有权；错误与日志不泄露凭证。

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
