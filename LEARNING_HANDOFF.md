# AI Agent 学习交接

更新时间：2026-09-12（Asia/Shanghai）

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
- Alembic 已包含 `a91c42e7d603` 用户凭证迁移与 `b62d19f804ae` 会话唯一索引兼容修复；当前 head 为 `b62d19f804ae`。
- 密码服务使用 Argon2id；正确密码、错误密码、随机盐、Unicode、空白和损坏哈希行为已验收。
- `RegisterRequest` 已完成：用户名去首尾空白、限制为 3～64 个 ASCII 字母/数字/下划线并转小写；密码为 8～128 字符、禁止所有 `str.isspace` 空白、不自动 trim，并用 `SecretStr` 承载。
- `create_registered_user` 已完成：生成 UUID4 `external_id`，只 `add/flush`，不 `commit`、不吞 `IntegrityError`；`get_user_by_username` 已存在。
- 当前还没有注册或登录 HTTP 接口，不能把模型或仓储测试当作接口验收。

### 最近验证结果

- 后端普通测试：`202 passed, 5 skipped`；Ruff 通过。
- 显式启用隔离 PostgreSQL 迁移测试：`207 passed`。
- 前端：`71 passed`，TypeScript 类型检查和 ESLint 通过。
- 本机 PostgreSQL 已升级至 `b62d19f804ae (head)`，`alembic check` 返回 `No new upgrade operations detected.`。
- 2026-09-12 已在 Windows 验证 Python 3.12、`pnpm 10.34.1`、PostgreSQL/pgvector 与 Redis 环境；现有 `.env` 和命名卷数据未被覆盖或删除。

测试数量只用于确认当前基线；新增课程后应以实际测试输出为准，不因数字变化误判回归。

## 唯一下一课

**第 4 周：注册服务的事务编排与用户名冲突分类。**

本课目标是在服务层串联已经完成的请求模型、密码哈希和用户仓储，形成清晰的事务边界。开始授课前先检查以下文件的实际内容：

- `apps/api/app/schemas.py`
- `apps/api/app/services/password_service.py`
- `apps/api/app/repositories/user_repository.py`
- `apps/api/app/models.py`
- `apps/api/tests/test_register_request.py`
- `apps/api/tests/test_registered_user_repository.py`

必须保持的设计边界：

- 仓储层继续只负责 `add/flush`，不提交事务，也不把所有 `IntegrityError` 都解释成用户名重复。
- 注册服务负责密码哈希、仓储调用以及成功提交、失败回滚。
- 只有能够确认是用户名唯一性冲突时，才转换成稳定的业务冲突；其他数据库完整性错误必须保留真实分类并向上传播。
- 密码明文、完整哈希、原始数据库异常和请求体不得进入日志或对外错误。
- `SecretStr` 只降低意外展示风险，不等于密码哈希，也不是完整的日志脱敏方案。
- 本课先完成服务层，不提前接入 HTTP 路由；接入注册接口前必须单独设计 Pydantic 验证错误与业务错误的脱敏响应。

学习者明确说“完成了”后，教练再检查实际实现并补齐测试。验收至少覆盖成功提交、重复用户名的稳定冲突与回滚、非用户名数据库错误不被误分类，以及事务失败后 Session 可继续使用。

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
