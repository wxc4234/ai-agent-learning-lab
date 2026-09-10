# AI Agent 学习交接

更新时间：2026-09-10（Asia/Shanghai）

这份文件只记录**当前进度、恢复方式和下一课**。完整路线统一查看 [LEARNING_PLAN.md](LEARNING_PLAN.md)，逐日任务与验收标准查看 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)。

目标岗位：**AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**。主项目目标是可观测、可恢复、可安全执行代码任务的 Codex-like Coding Agent；能力边界见 [docs/codex-like-agent-scope.md](docs/codex-like-agent-scope.md)。当前学习后端、数据和执行隔离是为了独立交付 Agent 产品，不是转向纯后端或模型训练岗位。

## 目录规则（长期有效）

`week-XX/` 只保存单周记录和一次性练习。任何会被后续周次继续开发的源码、配置、测试、环境说明或基础设施，都不得放入某个 `week-XX/` 目录。当前跨周 FastAPI 后端统一位于 `apps/api/`，后续 Next.js 前端放入 `apps/web/`。详细规则见 [AGENTS.md](AGENTS.md)。

## 当前进度

### 进度口径

当前日期为 2026-09-10，日历处于**第 3 周（09-07 ～ 09-13）**。第 1 周、第 2 周与第 3 周均已通过整周验收，所以正式周进度是 **3 / 12（25%）**。提前完成后继续实现的功能记入对应未来课程，但不会把尚未通过全部验收的周次标记为完成。

| 周次 | 状态 | 已完成与缺口 |
|---|---|---|
| 第 1 周 | 已完成 | Python、FastAPI、DeepSeek、SQLite、持久化对话和最小时间工具闭环 |
| 第 2 周 | 已完成 | 分层配置、PostgreSQL、SQLAlchemy、Alembic、pytest、Docker Compose、跨平台环境文档 |
| 第 3 周 | 已完成（当前日历周） | 流式 UI、停止/超时/跨实例取消、六态状态机、最小 Runtime（Registry、参数校验、顺序 Agent Loop）、结构化工具事件、`run_id` 落库、冒烟评测与复盘 |
| 第 4 周 | 部分预完成 | 可观测、成本/延迟摘要和 Token 上限保护已完成；失败摘要展示待收尾。认证、授权、Workspace、Task、会话恢复、Redis 幂等/限流未完成 |
| 第 5 周 | 未开始 | 安全文件/搜索/Shell/Apply Patch/Git/测试工具、Sandbox 与审批策略尚未开始 |
| 第 6 周 | 未开始 | 仓库扫描、符号/关键词/向量混合检索、代码引用、Context Builder 与检索评测尚未开始 |
| 第 7 周 | 未开始 | 高级 Runtime：Plan、Compaction、Memory、LangGraph Checkpoint、暂停/恢复、Reflection 与轨迹评测；基础 Runtime 已归入第 3 周，不重复学习 |
| 第 8 ～ 12 周 | 未开始 | MCP、Skills、审批、有限子 Agent、生产部署、Codex-like 产品收尾和求职按新路线推进 |

第 3 周已完成流式 Agent UI、停止生成、六态状态机、`run_id` 事件落库、5 条固定冒烟评测、事件流图与复盘；取消原因协议也已通过 Redis Pub/Sub 完成跨实例传播。

Agent Runtime 的非流式闭环已经完成：工具参数模型、JSON Schema 自动生成、显式工具白名单、通用调度循环、结构化错误观察、执行超时、取消传播和 DeepSeek 消息适配均已落地。`POST /tool-test` 已由手写两次调用重构为通用 Agent Loop，并通过真实 DeepSeek 请求验证“模型请求工具 → Runtime 执行 → 结果回传 → 最终回答”。

正式流式聊天的纵向集成也已完成：`stream_agent_loop` 产生领域事件，`POST /chat/stream` 将其编码为 NDJSON、同步写入运行时间线，Next.js BFF 原样转发，前端解析任意网络分块并显示工具参数、结果、失败原因和最终回答。真实 DeepSeek 请求已验证完整事件顺序。

2026-09-09 完成了 Agent 运行成本与延迟的后端可观测链路：从每次 DeepSeek 响应提取 Token usage 和模型耗时，在 Runtime 中跨步骤累计模型指标；记录成功、异常和超时工具调用的执行耗时；按照北京时间工作日高峰/空闲价格估算人民币费用；最终由 `RUN_FINISHED` 同时向数据库与浏览器发送 Token、模型耗时、工具耗时、`estimated_cost_cny` 和当次价格快照。

2026-09-10 完成了可观测指标的前端协议边界、状态建模与完成态展示：浏览器会严格校验 `RUN_FINISHED.metrics` 的嵌套结构、可空字段、非负整数、价格时段和人民币十进制字符串；正常完成时把步骤数与指标原子保存到 `runSummary`，并展示总 Token、费用、模型耗时、工具总耗时和步骤数。可空指标显示“暂无数据”，真实的 0 仍显示为 0；新请求、重试、取消和错误路径不保留旧指标。后端也已让 `TOOL_CALL_RESULT` / `TOOL_CALL_ERROR` 在 NDJSON 与运行时间线中共用同一份单次耗时 Payload，浏览器协议解析器会根据工具错误发生阶段严格校验耗时为非负安全整数或显式 `null`，聊天状态会继续保留运行中 `undefined`、执行后整数和执行前失败 `null` 三种语义。工具卡片现在会隐藏尚未产生的耗时、显示真实整数耗时，并把执行前错误解释为“未进入执行阶段”。后端 Token 预算策略已接入 Agent Runtime 与正式聊天服务：达到预算或 usage 未知的 ToolAction 会在工具开始前终止，最终答案优先交付；预算来自后端配置，不能由浏览器覆盖。成功与非成功 Agent 终态现在共用同一指标构造逻辑，预算和最大步数错误会在数据库与 NDJSON 中保留已经产生的步骤数、Token、费用和耗时；浏览器也已安全解析完整失败摘要，同时继续兼容没有摘要的普通错误。

当前代码已经具备：

- FastAPI + DeepSeek 对话接口。
- `session_id` 会话隔离和最近 5 轮上下文。
- Docker Compose 启动 PostgreSQL + pgvector 与 Redis，两个服务的健康检查通过。
- Redis 已验证读写；开启 AOF 和命名卷后重启容器，测试数据仍可读取。
- SQLAlchemy 数据模型：`users`、`conversations`、`messages`、`agent_runs`、`agent_run_events`。
- 已将旧 SQLite 的 4 个会话、10 条消息迁移到 PostgreSQL，并验证重复执行不会重复导入。
- `/chat` 与会话查询接口已切换为 PostgreSQL 持久化。
- Alembic 已初始化并接入 SQLAlchemy metadata；初始迁移为 `fed4e53cb0f7_create_agent_schema.py`。
- 当前 `agent_lab` 已标记到该迁移版本；并已在空数据库执行 `alembic upgrade head`，验证可创建 5 张业务表。
- 后端 123 个 pytest 测试全部通过：覆盖模型决策消息历史、Token 与耗时累计、Token 预算策略、Runtime 与聊天流预算终止、成功/失败共用指标 Payload、人民币费用与高峰/空闲边界、Observation 去重与防改写、多工具拒绝、Agent Loop 领域事件、单次工具耗时 Payload、结构化聊天流、取消回滚、工具、仓储及接口错误契约；自动化测试不调用模型 API。
- `apps/web` 已初始化 Next.js + TypeScript + Tailwind + App Router，`pnpm dev` 可启动。
- 已按 AG-UI 事件模型设计前端状态映射，见 `docs/agent-ui-events.md`。
- `POST /chat/stream` 已接入 Agent Loop，并以 `application/x-ndjson` 输出工具和文本事件；完整一轮结束后保存消息到 PostgreSQL。
- 无参数时间工具 `get_current_time`。
- 带参数的时间与矩形面积工具；Pydantic 参数模型同时作为运行时校验和模型 JSON Schema 的唯一来源。
- `ToolDefinition`、`TOOL_REGISTRY` 与通用 Agent Loop；未知工具、非法参数、执行异常和超时都会转成可追踪的 Observation。
- Agent Loop 具有最大步数和可选 Token 续跑预算限制，同步工具在线程中执行，并保持 `CancelledError` 向上传播。
- `DeepSeekDecisionMaker` 维护 system/user/assistant/tool 历史，保留 assistant `tool_calls`，并只追加新的 Observation；当前顺序策略会显式拒绝一轮多个工具调用。
- `POST /tool-test` 已接入通用 Agent Loop；Mock 与真实 DeepSeek 均验证了两步工具调用闭环。
- `stream_agent_loop` 在工具开始、成功、失败和循环终止时产生领域事件；工具成功、执行异常和超时事件携带非负整数耗时，未知工具和参数错误携带 `null`；原有 `run_agent_loop` 保持兼容。
- `DeepSeekDecisionMaker` 使用高精度单调时钟测量每次模型请求，并把 prompt/completion、缓存命中/未命中 Token 映射为通用 `ModelUsage`。
- Agent Runtime 会累计多步模型 usage、模型耗时和实际工具执行耗时；任一步缺失模型指标时保留 `None`，不把不完整数据伪装成 0。
- `token_budget.py` 已定义续跑预算纯策略：累计 Token 小于上限时允许继续，达到或超过上限时耗尽，usage 缺失时明确返回未知。
- `stream_agent_loop` 与 `run_agent_loop` 已支持可选 `max_total_tokens`：ToolAction 预算耗尽或 usage 未知时 fail-closed，且不会产生虚假的工具开始事件；FinalAnswer 不需要续跑，会正常完成。
- `AGENT_MAX_TOTAL_TOKENS` 默认 8000 且必须为正整数；正式聊天会把它传给 Runtime，并把预算耗尽或 usage 未知映射为稳定 `RUN_ERROR`，回滚未完成轮次且不允许浏览器覆盖预算。
- `build_run_metrics_payload` 是成功与非成功领域终态的公共指标出口；三种 Agent Loop 错误会携带 `steps_taken` 和 `metrics`，usage 未知时 Token 与费用继续保持 `null`。
- `model_pricing.py` 使用 `Decimal` 估算人民币费用，按照北京时间工作日 09:00-12:00、14:00-18:00 选择高峰价格，其余时段选择空闲价格。
- DeepSeek 人民币单价集中在 `Settings` 与 `.env.example`，`RUN_FINISHED` 保存 `estimated_cost_cny` 和当次模型、时段、单价快照，历史费用可以解释和复核。
- `apps/web` BFF 流代理已跑通：`/api/chat/stream` Route Handler 转发 `response.body`，浏览器逐块渲染，API Key 不出现在客户端。
- 前端 NDJSON 解析器可以处理半条、多条和最后一条无换行事件，严格校验 `RUN_FINISHED.metrics`、成对出现的 `RUN_ERROR` 失败摘要及工具事件单次耗时；`ToolActivity` 会保存成功、执行错误和执行前错误的真实耗时语义；完成态把步骤数和指标保存到 `runSummary`，只在 `done` 时显示运行摘要。工具卡片区分运行中、成功和失败，展示单次整数耗时或“未进入执行阶段”，工具失败不会提前结束整次运行。
- 停止生成：前端 `AbortController` + 「停止生成」按钮；BFF 用 `signal` 转发；后端 `stream_chat_reply` 捕获 `asyncio.CancelledError` 撤销未完成的一轮。
- 取消原因协议：浏览器通过 BFF 发送 `user` 或 `timeout`，FastAPI 将意图写入 PostgreSQL，并借助 Redis Pub/Sub 取消任意实例上承载该 run 的流任务。
- `interview-questions/` AI 全栈面试题库已建立（算法 / 前端 / 后端 / AI / 系统设计 / 项目 / 行为 七维度，与 `LEARNING_CURRICULUM.md` 第 4 章互补）。

已完成：36 条前端状态/协议/展示数据测试、Agent 事件流图和第 3 周复盘，分别见 `apps/web/test/features/chat/*.test.ts`、`docs/architecture.md` 与 `week-learning/week-03/REVIEW.md`。

当前收尾缺口：前端真实环境下的断网/限流手动验收；浏览器尚未把已经解析的失败运行摘要保存到状态并展示。Redis 目前只承担取消传播，尚未完成第 4 周要求的幂等、限流和短期状态能力。

## 当前文件

| 文件 | 作用 |
|---|---|
| `apps/api/app/main.py` | FastAPI 应用入口，注册路由并初始化数据库 |
| `apps/api/app/config.py` | DeepSeek、数据库、Redis、价格与 Agent Token 预算配置 |
| `apps/api/app/database.py` | SQLAlchemy Engine、Session 和 ORM 基类 |
| `apps/api/app/models.py` | 用户、会话、消息、Agent Run 与事件模型 |
| `apps/api/app/repositories/` | PostgreSQL 用户、会话与消息数据访问层 |
| `apps/api/app/repositories/run_repository.py` | Agent Run 创建、事件记录、终态更新与时间线查询 |
| `apps/api/app/services/run_cancellation.py` | Redis 取消信号发布、订阅与资源关闭 |
| `apps/api/app/services/agent_runtime.py` | Agent 决策、工具执行、Observation、最大步数与可选 Token 预算控制 |
| `apps/api/app/services/model_decision.py` | DeepSeek 消息历史与 `ToolAction` / `FinalAnswer` 决策适配 |
| `apps/api/app/services/model_pricing.py` | DeepSeek 高峰/空闲价格选择与人民币费用估算 |
| `apps/api/app/services/token_budget.py` | 判断累计 Token 是否允许 Agent 继续下一次模型调用 |
| `apps/api/app/services/tool_event_payloads.py` | 将 Runtime 工具观察映射为带单次耗时的公共事件 Payload |
| `apps/api/app/services/chat_service.py` | 会话上下文、Agent 事件到 NDJSON/运行事件的映射与完整一轮持久化 |
| `apps/api/app/tools/registry.py` | 工具参数模型、模型 Schema、执行器和显式白名单 |
| `apps/api/migrations/` | Alembic 表结构迁移历史 |
| `apps/api/alembic.ini` | Alembic 配置入口 |
| `apps/api/tests/` | pytest 自动化测试：工具、仓储与接口契约 |
| `apps/api/scripts/migrate_sqlite_to_postgres.py` | 一次性 SQLite 历史消息迁移脚本 |
| `docs/architecture.md` | 当前服务职责与请求、数据流向图 |
| `docs/agent-ui-events.md` | Agent 流式事件与前端状态映射 |
| `docs/codex-like-agent-scope.md` | Codex-like 主项目能力矩阵、最终端到端验收与明确边界 |
| `LEARNING_COACH_GUIDE.md` | 新会话恢复上下文、逐课教学、验收与进度维护规则 |
| `week-learning/` | 每周结束后的学习记录、练习与复盘 |
| `apps/web/` | 持续演进的 Next.js Agent 前端 |
| `apps/web/src/app/api/chat/stream/route.ts` | Next.js BFF 流代理 Route Handler |
| `apps/web/src/features/chat/components/chat-panel.tsx` | 流式聊天面板（含停止生成） |
| `apps/web/src/features/chat/agent-stream.ts` | NDJSON 分块读取、事件校验与前端类型定义 |
| `apps/web/src/features/chat/chat-state.ts` | 文本、终态与工具执行状态机 |
| `apps/web/src/features/chat/run-summary-view.ts` | 将完成态指标转换为稳定的展示数据 |
| `apps/web/src/features/chat/tool-duration-view.ts` | 将工具单次耗时的三态数据转换为展示文本 |
| `apps/web/src/features/chat/components/run-summary-card.tsx` | 仅在正常完成后渲染运行指标摘要 |
| `apps/web/test/features/chat/` | 前端状态、协议与展示数据测试 |
| `infra/compose.yaml` | 跨平台 PostgreSQL + pgvector、Redis 本地服务 |
| `interview-questions/` | AI 全栈面试题库（算法/前端/后端/AI/系统设计/项目/行为） |
| `ENVIRONMENT.md` | 安装、启动和常见问题 |

## 当前接口

| 接口 | 作用 |
|---|---|
| `GET /` | 服务健康检查 |
| `GET /chat` | 提示使用 POST |
| `POST /chat` | 带 PostgreSQL 记忆的 DeepSeek 对话 |
| `POST /chat/stream` | 运行 Agent Loop，以 NDJSON 返回工具/文本/终态事件并保存完整对话 |
| `POST /runs/{run_id}/cancel` | 记录取消原因，并通过 Redis 通知承载流的 API 实例 |
| `POST /api/chat/stream`（Next.js BFF） | 同源转发 FastAPI 流，浏览器只请求前端地址 |
| `POST /api/runs/{run_id}/cancel`（Next.js BFF） | 同源转发取消原因，浏览器不直接访问 FastAPI |
| `GET /sessions/{session_id}/messages` | 查询 PostgreSQL 会话历史 |
| `POST /tool-test` | 通过 DeepSeek 决策适配层和通用 Agent Loop 验证多步骤 Tool Calling |

## 下一课

本课配套状态（2026-09-10）：提前创建的 `consume-chat-response.test.ts` 已按用户要求删除。必须等学习者明确说本课“完成了”，教练才检查实际改动、创建对应测试并补齐配套、运行验收。`test:state` 保留用户明确要求的递归通配符优化；当前课程尚未验收，不推进进度。此次交接仅提交教学规则和测试发现脚本优化；未实现失败摘要核心逻辑，新会话应继续本课，不得将“提交并开启新会话”理解为本课已完成。

只扩展聊天状态和事件消费：指标型 `RUN_ERROR` 进入 `error` 时把步骤数与指标保存到 `runSummary`，普通错误继续保存 `null`。暂不修改运行摘要卡片的渲染条件。

下一课处理“终态事件不能丢数据”：当前 `chat-panel` 把 `RUN_ERROR` 转成普通 `Error` 后只保留消息，完整摘要会在 catch 中丢失。应直接分发带可选摘要的失败 action，并立即结束本次流消费；HTTP、网络和模型普通错误仍走现有 catch 路径。

验收标准：

- `fail` action 能可选携带完整 `CompletedRunSummary`；未携带时明确保存 `null`。
- 指标型 `RUN_ERROR` 直接进入 `error` 并原子保存错误消息与摘要，不再通过抛错丢失数据。
- 普通 `RUN_ERROR`、HTTP、网络与模型错误继续进入 `error`，且不会伪造摘要。
- 收到流内终态错误后立即结束消费，不再处理后续事件。
- 新请求、重试、取消仍会清除旧摘要，正常完成路径保持兼容。
- 前端类型检查、lint 和相关状态测试通过。

完成这一个小任务后，先补失败摘要卡片和断网/限流的浏览器手工验收；随后进入**第 4 周 Day 1：身份模型与后端认证**。第 4 周将按认证 → 前端登录态 → 授权与所有权 → Workspace/Task/会话恢复 → Redis 幂等/限流 → 可观测复核 → 全栈安全验收的顺序推进。

## 新会话教学入口

新会话先完整阅读 `AGENTS.md`、本文、`LEARNING_PLAN.md`、`LEARNING_CURRICULUM.md`、`docs/codex-like-agent-scope.md` 和 `LEARNING_COACH_GUIDE.md`，再检查 Git 状态与当前代码。不得从第 1 周重讲，也不得因为基础 Runtime 已完成就跳到第 7 周；当前第一课始终以本文“下一课”为准。讲课时不能只给残缺片段：核心参考实现直接放在对话代码块中，新文件给全文，已有文件只给修改段或完整函数，不给 Diff 或讲义链接。学习者亲手实现核心逻辑并明确说本课“完成了”后，教练才检查其改动、创建对应测试与机械性配套并验收；教练说明验证命令与预期结果。先读 LEARNING_COACH_GUIDE.md 第 7 节纠错记录，后续在已检出 main 的主仓库操作。

## 换电脑后恢复

Windows 首次使用：

```powershell
git clone https://github.com/wxc4234/ai-agent-learning-lab.git
cd ai-agent-learning-lab
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

已有仓库时：

```powershell
git pull origin main
.\.venv\Scripts\Activate.ps1
```

填写本机 `.env` 后启动：

```powershell
cd apps\api
python -m uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>。不要复制其他操作系统生成的 `.venv`。

前端（需后端已启动）：

```powershell
cd apps\web
pnpm install
Copy-Item .env.example .env   # 确认 API_BASE_URL 指向 http://127.0.0.1:8000
pnpm dev
```

打开 <http://localhost:3000>。

## 每次学习结束只更新这里

后续只需要维护三处内容：

- 当前进度。
- 下一课和验收标准。
- 新增的重要文件或启动变化。

不要再把完整 12 周路线复制到本文件。
