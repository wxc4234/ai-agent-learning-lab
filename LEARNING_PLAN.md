# AI Agent 应用开发工程师 12 周学习计划

方向：前端 / 全栈产品工程
更新时间：2026-08-31（Asia/Shanghai）

这是仓库中**唯一维护完整学习路线的文档**。逐日任务、验收清单、常见坑和面试题库放在 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)，其他 README 只负责导航或阶段记录。

## 1. 目标岗位

主要投递：

- AI Agent 前端工程师。
- AI Agent 应用开发工程师（偏前端）。
- Agent 产品工程师 / AI Product Engineer。
- AI Agent 全栈应用工程师。

不以这些岗位为主：

- 大模型算法工程师。
- 模型训练、微调或推理优化工程师。
- 纯 Agent 平台底层、纯后端或基础设施工程师。

目标是在 2026-11-15 前，能够独立完成一个有真实 Agent 交互、Python 服务、数据持久化、评测和部署的完整 AI 产品。

## 2. 能力投入比例

| 能力 | 时间占比 | 学习目标 |
|---|---:|---|
| Agent 产品前端 | 35% | 流式回答、工具过程、引用、审批、任务状态和错误恢复 |
| Agent / RAG 核心 | 30% | Tool Calling、状态、记忆、LangGraph、MCP、RAG 和评测 |
| Python 后端与数据 | 25% | 能独立实现 API、数据库、权限、缓存和后台任务 |
| 部署、安全与求职 | 10% | Docker、Trace、安全、作品集和面试表达 |

每日投入：**4 ～ 6 小时**（含周末折算）。剩余周期按 11 周计算（2026-08-31 ～ 2026-11-15）。投递不等作品集全部完成，第 8 周开始第一批投递，用面试反馈反向指导后续学习。

你的前端经验不是重新学习，而是升级为 **Agent UX + AI 全栈交付能力**。

## 3. 作品集总体架构

```text
Next.js / TypeScript
  ├── 对话与任务界面
  ├── streaming / tool events
  ├── 引用、审批、错误与恢复
  └── BFF 与登录态
            ↓ typed API + SSE/WebSocket
Python / FastAPI
  ├── Agent Runtime / LangGraph
  ├── 模型调用、Tool Calling、MCP
  ├── RAG 与权限校验
  └── 后台任务、评测与 Trace
            ↓
PostgreSQL + pgvector / Redis / Object Storage
```

### 各层职责

| 组件 | 负责什么 |
|---|---|
| Next.js | Agent 产品界面、BFF、流式事件展示、用户操作和审批 |
| FastAPI | Agent 执行、模型调用、RAG、权限和业务接口 |
| PostgreSQL | 用户、会话、消息、任务、工具记录等长期数据 |
| pgvector | 文档 Embedding、向量索引和相似度检索 |
| Redis | 缓存、限流、任务状态、锁和消息通知 |
| 对象存储 | 用户上传的原始文档和附件 |

## 4. 主技术栈

### 前端主线

- TypeScript、React、Next.js App Router。
- Server/Client Component、Route Handler 和 BFF。
- Vercel AI SDK 或等价的流式 UI 协议；**AG-UI 事件协议**作为「Agent UI 事件应该长什么样」的参考答案。
- SSE、WebSocket、AbortController、断线重连。
- Agent 事件状态机：thinking、tool pending、approval、running、success、error。
- 引用来源、工具参数、执行日志、Human-in-the-loop 和错误恢复界面。

### Agent 与后端主线

- Python、FastAPI、Pydantic、asyncio。
- PostgreSQL、SQLAlchemy、Alembic、Redis。
- 结构化输出、Tool Calling、Agent Loop、LangGraph、MCP。
- **Agent 原理四要素：Planning、Memory、Tool Use、Reflection**；ReAct 循环能手写。
- **Context Engineering**：控制每次推理进入上下文的全部内容（系统提示、历史、检索结果、工具返回、压缩策略）。
- RAG：解析、Chunk、Embedding、pgvector、混合检索、Rerank、引用和评测。
- **评测与可观测性**：Trace、轨迹评测、LLM-as-Judge、回归门禁、成本与延迟控制。
- pytest、Docker Compose、OpenTelemetry/Langfuse、CI/CD。

### 概念级了解（能讲清，不实现）

这些在面试中出现频率高，但实现成本与收益不匹配。各留 2 ～ 4 小时读文档并能画图说明即可：

- 第二个 Agent 框架（CrewAI 或 OpenAI Agents SDK 任选一个）：能说清与 LangGraph 的定位差异。
- 多 Agent 协作与路由：能画架构图，并说清什么场景不该用多 Agent。
- A2A 协议与 Agent Card：与 MCP 的分工差异。
- Computer Use / GUI Agent：与 Function Calling 的取舍。
- 其他向量库（Pinecone、Weaviate、Milvus、Chroma）：能说清为什么本项目选 pgvector。

### 暂缓内容

- 从零训练模型、复杂微调、CUDA 和深度强化学习。
- 深入实现两套以上 Agent 框架。招聘方明确把「框架堆名字」列为被高估的信号，深度比广度值钱。
- Kubernetes 深度运维和大规模分布式向量库调优。

## 5. macOS 与 Windows 通用方案

代码和运行环境遵守以下规则：

```text
GitHub：同步代码、配置模板和数据库迁移
Docker Compose：统一 PostgreSQL、pgvector、Redis 的版本
.venv：每台电脑单独创建
node_modules：每台电脑单独安装
Docker Volume：每台电脑保存自己的本地测试数据
```

数据库固定使用支持 `amd64` 和 `arm64` 的镜像：

```yaml
image: pgvector/pgvector:0.8.6-pg17-bookworm
```

需要记住：两个系统可以运行同一个项目，但本地数据库数据不会自动同步。学习阶段分别保存测试数据；需要共享数据时再连接同一个云端 PostgreSQL + pgvector。

## 6. 从 Codex 与 DeepSeek Harness 借鉴什么

不照搬它们的 Rust/TypeScript 大型架构，只学习可迁移的工程原则：

| 开源设计 | 在作品集中的实现 |
|---|---|
| Agent Loop 与界面分离 | Agent 执行放在 FastAPI/LangGraph，Next.js 只消费事件 |
| Session/Event 是事实来源 | 保存 user、assistant、tool、run、approval 等事件 |
| Tool Registry | 工具统一注册 Schema、执行函数和权限，不写大量 `if` |
| 工具执行管线 | 校验 → 权限 → 执行 → 超时/重试 → 记录 → 返回模型 |
| Streaming Events | 前端逐步展示模型、工具和任务状态 |
| Compaction 与记忆 | 长对话压缩、短期状态和长期用户记忆分开 |
| MCP / Skills | 用标准协议连接外部工具，而不是每次写专用胶水代码 |
| Approval / Sandbox | 危险工具先审批，代码执行放进受限环境 |
| Trace / Eval | 能还原 Agent 每一步，并用固定用例验证修改效果 |

pgvector 的选择不是照搬 Codex 或 DeepSeek Harness。它来自 RAG 项目与岗位需求，用于作品集中的知识检索；Harness 主要影响 Agent Runtime、工具和会话设计。

## 7. 12 周执行路线

| 周次 | 日期 | 学习重点 | 验收产物 |
|---|---|---|---|
| 第 1 周 | 08-24 ～ 08-30 | Python、FastAPI、DeepSeek、SQLite、最小 Tool Calling | 已完成持久化对话和时间工具闭环 |
| 第 2 周 | 08-31 ～ 09-06 | 后端底座与跨平台：分层、配置、异步、PostgreSQL、ORM、迁移、测试、Docker | FastAPI 成为分层项目；macOS/Windows 使用相同数据库环境 |
| 第 3 周 | 09-07 ～ 09-13 | Next.js Agent UI：流式回答、事件状态、停止/重试；**同周建立 run_id + 事件落库 + 冒烟评测** | Next.js 可流式调用 FastAPI；任意一次运行可按 run_id 还原 |
| 第 4 周 | 09-14 ～ 09-20 | Agent 产品全栈：登录、会话、Redis、权限、文件上传、工具卡片、**成本与延迟看板** | 可登录、可恢复会话、可看到工具过程与单次运行成本 |
| 第 5 周 | 09-21 ～ 09-27 | RAG 基础：解析、Chunk、Embedding、pgvector、引用 | 上传文档后可问答，并显示来源片段 |
| 第 6 周 | 09-28 ～ 10-04 | RAG 产品化：混合检索、Rerank、权限过滤、评测和检索调试 UI | 可以比较检索策略，并在界面检查召回结果 |
| 第 7 周 | 10-05 ～ 10-11 | Agent Runtime：**Planning / Memory / Tool Use / Reflection**、ReAct、Context Engineering、LangGraph、**轨迹评测** | 手写 ReAct 循环重构为可恢复状态图；工具选择正确率可度量 |
| 第 8 周 | 10-12 ～ 10-18 | MCP、A2A 概念、Human-in-the-loop、审批、Guardrails；**本周开始第一批投递** | MCP 工具可调用；审批三种操作可用；简历与讲解稿 v1 完成 |
| 第 9 周 | 10-19 ～ 10-25 | 生产化：后台任务、Trace 完善、评测入 CI、安全、Docker、云部署 | 有公网可访问地址；CI 自动跑测试与评测 |
| 第 10 周 | 10-26 ～ 11-01 | 主项目收尾（一）：产品化、失败路径、架构图 | 干净环境可启动；两个失败恢复场景可演示 |
| 第 11 周 | 11-02 ～ 11-08 | 主项目收尾（二）：评测报告、演示视频、README | 视频与评测数据完成；可选第二框架与多 Agent 补齐 |
| 第 12 周 | 11-09 ～ 11-15 | 面试强化、模拟面试、第二批定向投递 | 讲解稿终版、题库过完、第二批投递完成 |

第 3 ～ 9 周的产出必须长在同一套 `apps/api` + `apps/web` 代码库里。第 10 ～ 11 周**不是开始做项目，而是把已有代码收尾成作品集**。若到第 9 周末仍有余量，再考虑第二个独立项目。

## 8. 第 2 周详细计划（当前周）

当前只推进后端底座，不提前同时学习 Next.js 和向量检索。第 3 周及之后的逐日计划见 [LEARNING_CURRICULUM.md](LEARNING_CURRICULUM.md)。

### Day 1：配置管理

- 学习：配置是什么，为什么不能散落在路由代码中。
- 实践：使用 Pydantic Settings 管理 API Key、模型名和数据库地址。
- 验收：缺少必要配置时提示清楚，`POST /chat` 仍然正常。

### Day 2：项目分层与依赖

- 拆分 routes、schemas、services、repositories、tools 和 config。
- 一次只移动一个职责，每次移动后都启动验证。

### Day 3：PostgreSQL 与 SQLAlchemy

- 学习表、主键、外键、索引和事务。
- 创建 users、conversations、messages 和 run_events 表。
- 将聊天记录从 SQLite 迁移到 PostgreSQL。

### Day 4：Alembic 迁移

- 创建、升级和回退数据库迁移。
- 验收新电脑能通过迁移得到相同表结构。

### Day 5：pytest

- 测试配置、工具函数、数据库读写和主要接口错误。
- 不在自动测试中真实消耗模型额度。

### Day 6：Docker Compose 跨平台环境

- 使用同一份 Compose 启动 PostgreSQL + pgvector 和 Redis。
- 在 macOS 验证后，保证 Windows 只需要相同命令即可启动。

### Day 7：复盘

- 更新环境文档和启动命令。
- 画出 Next.js → FastAPI → Agent → 数据库/模型的架构图。
- 提交本周成果，记录仍未解决的问题。

## 9. 作品集

### 主项目：可观测的企业知识与任务 Agent

原「项目一 + 项目二」合并为一个足够深的项目。合并原因：两者共用约 80% 技术栈，而招聘方更看重 `build → measure → refine` 的具体痕迹——一个有真实评测数据、Trace 和失败恢复演示的项目，胜过两个只有顺利路径的 Demo。

前端差异化能力（第 3 ～ 4 周产出）：

- 流式回答与停止生成。
- Plan、Tool Call、Tool Result 时间线。
- 工具参数和结构化结果卡片。
- 加载、超时、重试、断线和恢复状态。
- 会话列表、运行历史、Trace 和 Token/耗时/成本展示。
- 高风险工具的批准、修改参数后批准和拒绝界面。

Agent 与数据能力（第 5 ～ 9 周产出）：

- 登录、知识库和文档权限（检索阶段前置过滤）。
- 异步文档解析和任务进度。
- pgvector + 混合检索 + Rerank + 答案引用。
- ReAct 循环重构为 LangGraph 状态图，支持 Checkpoint 恢复。
- 至少 3 个 MCP 工具，其中至少 1 个自己实现的 Server。
- 检索评测 + 轨迹评测 + LLM-as-Judge，入 CI 作为回归门禁。
- Trace、审计记录、失败恢复和成本上限。

### 可选第二项目（第 9 周末评估后再决定）

只有在主项目已达到「可运行、可部署、有评测数据、有演示视频」之后才启动。候选方向按前端优势排序：简化版 Agent Workflow Builder（节点编排 + 运行调试）、或多 Agent 协作演示。不追求复制 Dify 或 Flowise。

### 交付标准（每个项目）

- README：一句话定位、截图或 GIF、架构图、启动命令、已知限制。
- 干净环境按 README 能启动，且有公网可访问地址。
- 3 ～ 5 分钟演示视频，必须覆盖至少两个失败恢复路径。
- 评测报告：策略对比表 + 一个「评测发现了回归」的具体例子。
- 明确写出没做什么以及为什么不做。

## 10. 求职验收

投递前必须能独立演示和解释：

- Agent UI 与普通聊天 UI 有什么区别。
- 如何把模型 token、tool call、approval 和错误转换成前端状态。
- Next.js 和 FastAPI 各自负责什么，如何保持类型契约。
- Tool Calling、MCP、LangGraph 的完整执行链路。
- Planning、Memory、Tool Use、Reflection 四要素在你的代码里分别落在哪。
- Context Engineering：一次推理的上下文由哪几部分拼成，超长时压缩掉什么。
- PostgreSQL、pgvector、Redis 分别保存什么。
- RAG 召回差时如何定位问题，并在界面上提供可调试信息。
- **你怎么知道改动之后变好了；你怎么知道你的评测本身可信。**
- 如何处理超时、重试、重复提交、权限和 Prompt Injection。
- 单次运行的成本和延迟是多少，怎么控制上限。
- 如何在 macOS、Windows 和服务器运行同一套项目。

倒数第三条是招聘方明确点出的第一区分点，必须能用具体数字和一个真实案例回答，不能只讲方法论。

最低作品集要求：主项目可运行、可部署、可演示，包含架构图、测试、评测结果和问题复盘。

## 11. 文档关系

- [项目入口](README.md)
- [逐日大纲、验收与面试题库](LEARNING_CURRICULUM.md)
- [仓库与跨周目录规则](AGENTS.md)
- [环境安装与启动](ENVIRONMENT.md)
- [岗位与开源项目校准依据](SKILL_GAP_ANALYSIS.md)
- [当前进度与跨电脑交接](LEARNING_HANDOFF.md)
- [第 1 周完成记录](week-01/README.md)

目录约束：`week-XX/` 只保存单周记录和一次性练习；跨周持续演进的应用、配置、测试、环境文档与基础设施不得放入周目录。
