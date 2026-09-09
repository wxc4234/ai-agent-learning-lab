# AI Agent 学习交接

更新时间：2026-09-09（Asia/Shanghai）

这份文件只记录**当前进度、恢复方式和下一课**。完整路线统一查看 [LEARNING_PLAN.md](LEARNING_PLAN.md)，逐日任务与验收标准查看 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)。

目标岗位：**AI Agent 应用开发工程师（偏前端 / 全栈产品工程）**。当前学习后端是为了能够独立交付 Agent 产品，不是转向纯后端或泛化的大模型应用岗位。

## 目录规则（长期有效）

`week-XX/` 只保存单周记录和一次性练习。任何会被后续周次继续开发的源码、配置、测试、环境说明或基础设施，都不得放入某个 `week-XX/` 目录。当前跨周 FastAPI 后端统一位于 `apps/api/`，后续 Next.js 前端放入 `apps/web/`。详细规则见 [AGENTS.md](AGENTS.md)。

## 当前进度

第 1 周、第 2 周与第 3 周已完成。第 3 周已经完成流式 Agent UI、停止生成、六态状态机、`run_id` 事件落库、5 条固定冒烟评测、事件流图与复盘；取消原因协议也已通过 Redis Pub/Sub 完成跨实例传播。

Agent Runtime 的非流式闭环已经完成：工具参数模型、JSON Schema 自动生成、显式工具白名单、通用调度循环、结构化错误观察、执行超时、取消传播和 DeepSeek 消息适配均已落地。`POST /tool-test` 已由手写两次调用重构为通用 Agent Loop，并通过真实 DeepSeek 请求验证“模型请求工具 → Runtime 执行 → 结果回传 → 最终回答”。

正式流式聊天的纵向集成也已完成：`stream_agent_loop` 产生领域事件，`POST /chat/stream` 将其编码为 NDJSON、同步写入运行时间线，Next.js BFF 原样转发，前端解析任意网络分块并显示工具参数、结果、失败原因和最终回答。真实 DeepSeek 请求已验证完整事件顺序。

2026-09-09 完成了 Agent 运行成本与延迟的后端可观测链路：从每次 DeepSeek 响应提取 Token usage 和模型耗时，在 Runtime 中跨步骤累计模型指标；记录成功、异常和超时工具调用的执行耗时；按照北京时间工作日高峰/空闲价格估算人民币费用；最终由 `RUN_FINISHED` 同时向数据库与浏览器发送 Token、模型耗时、工具耗时、`estimated_cost_cny` 和当次价格快照。

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
- 后端 83 个 pytest 测试全部通过：覆盖模型决策消息历史、Token 与耗时累计、人民币费用与高峰/空闲边界、Observation 去重与防改写、多工具拒绝、Agent Loop 领域事件、结构化聊天流、取消回滚、工具、仓储及接口错误契约；自动化测试不调用模型 API。
- `apps/web` 已初始化 Next.js + TypeScript + Tailwind + App Router，`pnpm dev` 可启动。
- 已按 AG-UI 事件模型设计前端状态映射，见 `docs/agent-ui-events.md`。
- `POST /chat/stream` 已接入 Agent Loop，并以 `application/x-ndjson` 输出工具和文本事件；完整一轮结束后保存消息到 PostgreSQL。
- 无参数时间工具 `get_current_time`。
- 带参数的时间与矩形面积工具；Pydantic 参数模型同时作为运行时校验和模型 JSON Schema 的唯一来源。
- `ToolDefinition`、`TOOL_REGISTRY` 与通用 Agent Loop；未知工具、非法参数、执行异常和超时都会转成可追踪的 Observation。
- Agent Loop 具有最大步数限制，同步工具在线程中执行，并保持 `CancelledError` 向上传播。
- `DeepSeekDecisionMaker` 维护 system/user/assistant/tool 历史，保留 assistant `tool_calls`，并只追加新的 Observation；当前顺序策略会显式拒绝一轮多个工具调用。
- `POST /tool-test` 已接入通用 Agent Loop；Mock 与真实 DeepSeek 均验证了两步工具调用闭环。
- `stream_agent_loop` 在工具开始、成功、失败和循环终止时产生领域事件；原有 `run_agent_loop` 保持兼容。
- `DeepSeekDecisionMaker` 使用高精度单调时钟测量每次模型请求，并把 prompt/completion、缓存命中/未命中 Token 映射为通用 `ModelUsage`。
- Agent Runtime 会累计多步模型 usage、模型耗时和实际工具执行耗时；任一步缺失模型指标时保留 `None`，不把不完整数据伪装成 0。
- `model_pricing.py` 使用 `Decimal` 估算人民币费用，按照北京时间工作日 09:00-12:00、14:00-18:00 选择高峰价格，其余时段选择空闲价格。
- DeepSeek 人民币单价集中在 `Settings` 与 `.env.example`，`RUN_FINISHED` 保存 `estimated_cost_cny` 和当次模型、时段、单价快照，历史费用可以解释和复核。
- `apps/web` BFF 流代理已跑通：`/api/chat/stream` Route Handler 转发 `response.body`，浏览器逐块渲染，API Key 不出现在客户端。
- 前端 NDJSON 解析器可以处理半条、多条和最后一条无换行事件；工具卡片区分运行中、成功和失败，工具失败不会提前结束整次运行。
- 停止生成：前端 `AbortController` + 「停止生成」按钮；BFF 用 `signal` 转发；后端 `stream_chat_reply` 捕获 `asyncio.CancelledError` 撤销未完成的一轮。
- 取消原因协议：浏览器通过 BFF 发送 `user` 或 `timeout`，FastAPI 将意图写入 PostgreSQL，并借助 Redis Pub/Sub 取消任意实例上承载该 run 的流任务。
- `interview-questions/` AI 全栈面试题库已建立（算法 / 前端 / 后端 / AI / 系统设计 / 项目 / 行为 七维度，与 `LEARNING_CURRICULUM.md` 第 4 章互补）。

已完成：8 条前端状态/协议测试、Agent 事件流图和第 3 周复盘，分别见 `apps/web/src/features/chat/*.test.ts`、`docs/architecture.md` 与 `week-learning/week-03/REVIEW.md`。

仍待补充：前端真实环境下的断网/限流手动验收；前端尚未解析和展示新的运行指标，工具结果/错误事件也尚未携带单次工具耗时。

## 当前文件

| 文件 | 作用 |
|---|---|
| `apps/api/app/main.py` | FastAPI 应用入口，注册路由并初始化数据库 |
| `apps/api/app/database.py` | SQLAlchemy Engine、Session 和 ORM 基类 |
| `apps/api/app/models.py` | 用户、会话、消息、Agent Run 与事件模型 |
| `apps/api/app/repositories/` | PostgreSQL 用户、会话与消息数据访问层 |
| `apps/api/app/repositories/run_repository.py` | Agent Run 创建、事件记录、终态更新与时间线查询 |
| `apps/api/app/services/run_cancellation.py` | Redis 取消信号发布、订阅与资源关闭 |
| `apps/api/app/services/agent_runtime.py` | Agent 决策、工具执行、Observation 与最大步数控制 |
| `apps/api/app/services/model_decision.py` | DeepSeek 消息历史与 `ToolAction` / `FinalAnswer` 决策适配 |
| `apps/api/app/services/model_pricing.py` | DeepSeek 高峰/空闲价格选择与人民币费用估算 |
| `apps/api/app/services/chat_service.py` | 会话上下文、Agent 事件到 NDJSON/运行事件的映射与完整一轮持久化 |
| `apps/api/app/tools/registry.py` | 工具参数模型、模型 Schema、执行器和显式白名单 |
| `apps/api/migrations/` | Alembic 表结构迁移历史 |
| `apps/api/alembic.ini` | Alembic 配置入口 |
| `apps/api/tests/` | pytest 自动化测试：工具、仓储与接口契约 |
| `apps/api/scripts/migrate_sqlite_to_postgres.py` | 一次性 SQLite 历史消息迁移脚本 |
| `docs/architecture.md` | 当前服务职责与请求、数据流向图 |
| `docs/agent-ui-events.md` | Agent 流式事件与前端状态映射 |
| `week-learning/` | 每周结束后的学习记录、练习与复盘 |
| `apps/web/` | 持续演进的 Next.js Agent 前端 |
| `apps/web/src/app/api/chat/stream/route.ts` | Next.js BFF 流代理 Route Handler |
| `apps/web/src/features/chat/components/chat-panel.tsx` | 流式聊天面板（含停止生成） |
| `apps/web/src/features/chat/agent-stream.ts` | NDJSON 分块读取、事件校验与前端类型定义 |
| `apps/web/src/features/chat/chat-state.ts` | 文本、终态与工具执行状态机 |
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

在 Windows 上继续完成成本与延迟可观测性的前端闭环：扩展 `AgentStreamEvent` 对 `RUN_FINISHED.metrics` 的运行时校验和 TypeScript 类型，把指标保存进聊天状态，并在完成态摘要中展示总 Token、人民币估算费用、模型耗时、工具耗时和步骤数。同时让 `TOOL_CALL_RESULT` / `TOOL_CALL_ERROR` 携带并展示单次工具耗时。

后端已经拥有完整数据，下一课的重点是协议边界和前端状态建模：不能直接信任网络 JSON，不能把缺失的 usage 或费用显示成 0，也不能让新增指标改变现有 done/error/aborted 终态语义。

验收标准：

- 浏览器能解析完整指标和 `model_usage: null` 两条路径，非法嵌套字段会被协议解析器拒绝。
- 完成态显示总 Token、人民币估算费用、模型耗时、工具耗时和步骤数；缺少 usage 或费用时显示“暂无数据”。
- 工具成功、执行异常和超时卡片显示单次耗时；未知工具和参数校验错误不伪造执行耗时。
- `RUN_FINISHED` 仍只触发一次 `done` 终态，停止生成和错误路径行为保持不变。
- 前端状态测试、lint、类型检查和生产构建全部通过，后端 83 个测试继续通过。

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
