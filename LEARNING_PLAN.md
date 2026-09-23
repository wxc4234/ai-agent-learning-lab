# AI Agent 应用开发工程师 12 周学习计划

方向：前端 / 全栈产品工程
更新时间：2026-09-23（Asia/Shanghai）

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

2026-09-15 路线调整：主产品为本地优先的 PC Coding Agent，Web UI 与执行服务运行在用户电脑。模型可使用用户自己的云端 API，后续再支持本地模型；不强制产品注册登录，不推进在线访客模式。域名用于展示/文档/下载，公开执行服务为可选扩展。已完成认证内容保留，默认教学方式仍由学习者实现核心代码。

目标是在 2026-11-15 前，能够独立完成一个**可观测、可恢复、可安全执行代码任务的 Codex-like Coding Agent**，并用它完整覆盖 AI 全栈产品所需的前端、后端、Agent Runtime、数据、检索、执行隔离、评测和部署能力。

“Codex-like”指复现可验证的产品与工程能力：用户提交代码任务后，Agent 能读取仓库指令与上下文、制定计划、调用受控文件/搜索/Shell/补丁/Git 工具、流式汇报进度、请求高风险操作审批、运行测试、展示 Diff，并能在中断后恢复。它不表示复刻 OpenAI 的专有模型、训练体系或云端基础设施。最终能力边界与验收场景见 [docs/codex-like-agent-scope.md](docs/codex-like-agent-scope.md)。

教学执行规则统一见 [AGENTS.md](AGENTS.md)，授课模板按需见 [LEARNING_COACH_GUIDE.md](LEARNING_COACH_GUIDE.md)。本文件不记录逐课实现与测试结果。

## 2. 能力投入比例

| 能力 | 时间占比 | 学习目标 |
|---|---:|---|
| Agent 产品前端 | 25% | 任务、计划、流式过程、终端输出、Diff、审批和错误恢复 |
| Agent Runtime 与工具 | 30% | Tool Calling、计划、状态、记忆、Checkpoint、MCP、Skills 和多 Agent |
| Python 后端与数据 | 20% | API、认证授权、数据库、缓存、队列和后台任务 |
| 代码上下文与检索 | 15% | 仓库扫描、符号/关键词检索、Embedding、混合检索、引用与压缩 |
| 部署、安全、评测与求职 | 10% | Sandbox、Trace、Evals、CI/CD、本地交付、作品集和面试表达 |

每日投入：**4 ～ 6 小时**（含周末折算）。12 周总周期为 2026-08-24 ～ 2026-11-15。投递不等作品集全部完成，第 8 周开始第一批投递，用面试反馈反向指导后续学习。

你的前端经验不是重新学习，而是升级为 **Agent UX + AI 全栈交付能力**。

## 3. 作品集总体架构

```text
Next.js / TypeScript
  ├── Workspace / Task / Conversation 界面
  ├── Plan / streaming / tool / terminal 事件
  ├── 文件树、引用、Diff Review 与审批
  └── 本地 BFF、连接状态、错误与恢复
            ↓ typed API + SSE/WebSocket
Python / FastAPI
  ├── Agent Runtime / LangGraph / Checkpoint
  ├── 模型适配、Tool Gateway、MCP / Skills
  ├── Context Builder、代码检索与权限策略
  └── 任务队列、评测、Trace 与成本控制
            ↓
Workspace Sandbox / Git / File / Search / Shell / Apply Patch
            ↓
PostgreSQL + pgvector / Redis / Object Storage / Worker
```

### 各层职责

| 组件 | 负责什么 |
|---|---|
| Next.js | Agent 产品界面、BFF、任务/计划/工具/终端/Diff 展示、用户操作和审批 |
| FastAPI | Agent Runtime、模型适配、上下文组装、权限策略和业务接口 |
| 执行 Worker / Sandbox | 在隔离 Workspace 中执行文件、搜索、Shell、补丁、Git 与测试工具 |
| PostgreSQL | 用户、Workspace、Task、Conversation、Run、Event、Approval 等长期事实 |
| pgvector | 代码/文档 Embedding、向量索引和语义检索 |
| Redis | 缓存、限流、幂等、任务协调、锁和消息通知 |
| 对象存储 | Workspace 压缩包、附件、日志与评测产物 |

## 4. 主技术栈

### 前端主线

- TypeScript、React、Next.js App Router。
- Server/Client Component、Route Handler 和 BFF。
- Vercel AI SDK 或等价的流式 UI 协议；**AG-UI 事件协议**作为「Agent UI 事件应该长什么样」的参考答案。
- SSE、WebSocket、AbortController、断线重连。
- Agent 事件状态机：queued、planning、running、waiting_approval、done、aborted、error。
- 任务列表、计划步骤、代码引用、工具参数、终端日志、Diff Review、Human-in-the-loop 和错误恢复界面。

### Agent 与后端主线

- Python、FastAPI、Pydantic、asyncio。
- PostgreSQL、SQLAlchemy、Alembic、Redis。
- 结构化输出、Tool Calling、Agent Loop、LangGraph、Checkpoint、MCP、Skills 和有限多 Agent 委派。
- **Agent 原理四要素：Planning、Memory、Tool Use、Reflection**；ReAct 循环能手写。
- **Context Engineering**：控制每次推理进入上下文的全部内容（系统提示、历史、检索结果、工具返回、压缩策略）。
- Coding Tools：安全文件读写、`rg`/符号搜索、Shell、Apply Patch、Git Diff、测试执行和结果回灌。
- 代码上下文/RAG：仓库扫描、Chunk、Embedding、pgvector、混合检索、Rerank、代码引用和评测。
- 安全执行：Workspace 路径边界、隔离环境、最小权限、审批、超时、资源/输出限制和密钥脱敏。
- **评测与可观测性**：Trace、轨迹评测、LLM-as-Judge、回归门禁、成本与延迟控制。
- pytest、前端测试、Docker Compose、任务 Worker、OpenTelemetry/Langfuse、CI/CD。

### 概念级了解（能讲清，不实现）

这些在面试中出现频率高，但实现成本与收益不匹配。各留 2 ～ 4 小时读文档并能画图说明即可：

- 第二个 Agent 框架（CrewAI 或 OpenAI Agents SDK 任选一个）：能说清与 LangGraph 的定位差异。
- A2A 协议与 Agent Card：与 MCP 的分工差异。
- Computer Use / GUI Agent：与 Function Calling 的取舍；本项目只做可选演示，不把它当主线。
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

## 6. Codex-like 产品必须复现的工程能力

不照搬专有实现或大型云架构，只实现可验证、可迁移的工程能力：

| 能力 | 在作品集中的实现 |
|---|---|
| Agent Loop 与界面分离 | Agent 执行放在 FastAPI/LangGraph，Next.js 只消费稳定领域事件 |
| Task / Run / Event 是事实来源 | 保存输入、计划、模型输出、工具、审批、补丁、测试和终态事件 |
| Tool Gateway | 工具统一注册 Schema、执行器、风险级别与权限，不允许模型直接选择可执行代码 |
| Coding Tools | 支持文件读取、搜索、Shell、Apply Patch、Git Diff 与测试，并把结构化结果回灌模型 |
| Streaming 与 Steering | 前端实时展示进度，支持取消、重试、等待审批和恢复，不用 HTTP 状态伪装流内错误 |
| Context / Compaction / Memory | 控制仓库指令、代码检索、历史、工具结果的 Token 占比；长任务可压缩续跑 |
| MCP / Skills / Instructions | 用 MCP 扩展外部工具，用 Skill 和仓库级指令封装可复用流程 |
| Approval / Sandbox | 高风险操作服务端强制审批，执行发生在受路径、资源、环境变量约束的 Workspace |
| Diff / Review / Verification | 任何改动都能查看 Diff，并执行目标测试、类型检查或 lint 后再交付 |
| Trace / Eval / Cost | 能按 run_id 还原轨迹，用固定任务集度量成功率、工具选择、补丁正确率、成本和延迟 |
| Durable Task / Delegation | 长任务可排队、暂停、恢复；仅把独立子任务委派给有限并发的子 Agent |

官方 OpenAI API 已公开呈现自定义工具、Shell、Apply Patch、MCP、Skills、流式事件、Compaction、Steering 与 Evals 等能力形态，本路线用这些公开边界校准作品集，不依赖某个闭源内部实现。来源见 [Responses API](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses)、[Evals API](https://developers.openai.com/api/reference/java/resources/evals/methods/create) 与 [Skills API](https://developers.openai.com/api/reference/python/resources/skills/methods/create)。

## 7. 12 周执行路线

| 周次 | 日期 | 学习重点 | 验收产物 | 当前状态 |
|---|---|---|---|---|
| 第 1 周 | 08-24 ～ 08-30 | Python、FastAPI、DeepSeek、SQLite、最小 Tool Calling | 持久化对话和时间工具闭环 | 已完成 |
| 第 2 周 | 08-31 ～ 09-06 | 后端底座与跨平台：分层、配置、异步、PostgreSQL、ORM、迁移、测试、Docker | FastAPI 成为分层项目；macOS/Windows 使用相同数据库环境 | 已完成 |
| 第 3 周 | 09-07 ～ 09-13 | 流式 Agent UI + **最小 Agent Runtime**：状态、取消、Tool Registry、参数校验、顺序循环、结构化事件、run_id 与冒烟评测 | 浏览器能观察并取消真实多步骤工具运行；任意运行可按 run_id 还原 | 已完成全部验收 |
| 第 4 周 | 09-14 ～ 09-20 | 本地工作台：Workspace、Task、会话恢复、幂等、执行互斥与并发预算 | PC 工作台及有边界的历史删除、进程退出恢复 | 已完成 |
| 第 5 周 | 09-21 ～ 09-27 | **Coding Tools + Sandbox**：文件、搜索、Shell、Apply Patch、Git Diff、测试、审批策略 | Agent 在隔离样例仓库完成一次小改动，展示 Diff 并通过测试 | 进行中；阶段能力见课程大纲，接续见交接 |
| 第 6 周 | 09-28 ～ 10-04 | **代码上下文工程 + RAG**：仓库扫描、符号/关键词/向量检索、混合排序、引用、Token 裁剪和检索评测；Obsidian Vault 只读接入实践 | Agent 能为跨文件任务找到正确上下文，并说明引用来源；能检索授权笔记库并给出可核验引用 | 未开始 |
| 第 7 周 | 10-05 ～ 10-11 | **高级 Agent Runtime**：Plan、Memory、Compaction、Reflection、LangGraph Checkpoint、暂停/恢复、轨迹评测 | 长任务可中断恢复；失败后能修正；轨迹指标可度量 | 未开始；基础 Runtime 已在第 3 周完成 |
| 第 8 周 | 10-12 ～ 10-18 | **可扩展与安全协作**：MCP、Skills/仓库指令、审批三态、有限子 Agent 委派、Prompt Injection 防护；开始投递 | 自建 MCP 与 Skill 可用；危险操作必须审批；独立子任务可安全合并 | 未开始 |
| 第 9 周 | 10-19 ～ 10-25 | **生产化**：后台 Worker、并发控制、Trace、成本路由、评测入 CI、安全、本地交付与受限云部署 | 干净 PC 可启动；有展示/下载入口；CI 自动评测；重启和超预算均可恢复或解释 | 未开始 |
| 第 10 周 | 10-26 ～ 11-01 | Codex-like 产品收尾：任务列表、Plan、文件树、终端、Diff Review、审批、失败恢复与响应式 UI | 一条真实代码任务可从输入走到已验证 Diff，完整过程可视化 | 未开始 |
| 第 11 周 | 11-02 ～ 11-08 | 质量证明：红队、性能、评测报告、演示视频、README、架构与限制 | 有成功率/成本/延迟数据和失败案例；陌生人可按 README 复现 | 未开始 |
| 第 12 周 | 11-09 ～ 11-15 | 面试强化、模拟面试、第二批定向投递 | 讲解稿终版、题库过完、第二批投递完成 | 未开始 |

第 3 ～ 9 周的产出必须长在同一套 `apps/api` + `apps/web` 代码库里。第 10 ～ 11 周**不是开始做项目，而是把已有代码收尾成作品集**。若到第 9 周末仍有余量，再考虑第二个独立项目。

Obsidian 接入已纳入本项目实践：当前 Sandbox 执行链路完成后，第 6 周先复用 Workspace 与文件工具实现 Vault 选择、关键词检索、受限读取及来源引用；第 8 周在 Diff、审批和并发冲突检测具备后接入笔记写入。双向链接、标签和语义检索按实际需求增强，Obsidian 内聊天及专用插件/MCP 集成为可选扩展。它是自研 Agent 的知识库使用场景，不新增独立项目或必修工具搭建课；分阶段范围与验收见 LEARNING_CURRICULUM.md 的“Obsidian Vault 接入实践”。

> 状态列按整周验收计算：截至 2026-09-20，正式周进度为 4 / 12（33.3%）。提前实现只记为预完成，不能代替该周其他验收。详细完成项、缺口和下一课以 [LEARNING_HANDOFF.md](LEARNING_HANDOFF.md) 为准。

## 8. 已完成周次

第 1～3 周见 [周记录](week-learning/README.md)，第 2 周原始逐日计划已移至 [历史学习记录](docs/history/learning-through-2026-09-23.md#第-2-周原始逐日计划)。完成课程不重复教学；正式周状态只在本文件的路线表维护。

## 9. 作品集

### 主项目：可观测、可恢复的 Codex-like Coding Agent

原「项目一 + 项目二」合并为一个足够深的项目。合并原因：两者共用约 80% 技术栈，而招聘方更看重 `build → measure → refine` 的具体痕迹——一个有真实评测数据、Trace 和失败恢复演示的项目，胜过两个只有顺利路径的 Demo。

前端差异化能力：

- Workspace、任务与会话列表；刷新后恢复上下文。
- 流式回答、停止、重试与 Steering 状态。
- Plan、Tool Call、Tool Result、Terminal 与 Approval 时间线。
- 工具参数、结构化结果、代码引用和失败原因卡片。
- 文件树、Diff Review、测试结果与接受/拒绝改动界面。
- 加载、超时、重试、断线和恢复状态。
- 运行历史、Trace 和 Token/耗时/成本展示。
- 高风险工具的批准、修改参数后批准和拒绝界面。

Agent、执行与数据能力：

- 本机稳定身份、Workspace/Task/Run 范围控制和完整审计记录；账号模式作为保留扩展。
- 安全文件、搜索、Shell、Apply Patch、Git 与测试工具；执行发生在受控 Workspace。
- 仓库指令、代码索引、pgvector + 混合检索 + Rerank + 精确代码引用。
- ReAct 基础循环重构为 LangGraph 状态图，支持 Checkpoint、暂停审批和恢复。
- MCP Client + 至少 1 个自建 Server；至少 1 个可复用 Skill；有限子 Agent 委派。
- 检索评测 + 补丁任务评测 + 轨迹评测，入 CI 作为回归门禁。
- Trace、成本/延迟、预算、并发、超时、取消、幂等、失败恢复和密钥脱敏。

最终端到端验收不是“聊天能回复”，而是：用户启动本地工作台、配置模型并选择本地 Workspace，提交一个跨文件代码任务；Agent 读取仓库指令和相关代码，生成可见计划，调用受控工具修改文件，必要时等待审批，运行测试，失败后修正，最终展示带引用的说明与可审查 Diff；服务重启或浏览器刷新后仍能恢复任务，整条轨迹可评测、可追踪、可解释成本。

保留一次受限云部署实践：部署独立样例服务，覆盖 HTTPS、反向代理、环境配置、日志、健康检查和发布回滚；不得直接公开本地免登录执行接口。

最终分发目标：普通体验者无需手动安装或维护 Docker、PostgreSQL、Redis；由产品内嵌存储、替代实现或受管理的运行依赖提供能力。当前源码版尚未达到此目标。存储/取消机制调整必须验证迁移、事务、备份恢复和跨进程行为，不能把数据库文件替换等同于完成适配。

### 可选第二项目（第 9 周末评估后再决定）

只有在主项目已达到「可运行、可部署、有评测数据、有演示视频」之后才启动。候选方向按前端优势排序：简化版 Agent Workflow Builder（节点编排 + 运行调试）、或多 Agent 协作演示。不追求复制 Dify 或 Flowise。

### 交付标准（每个项目）

- README：一句话定位、截图或 GIF、架构图、启动命令、已知限制。
- 干净环境按 README 能启动，且有介绍/文档/下载入口。
- 3 ～ 5 分钟演示视频，必须覆盖至少两个失败恢复路径。
- 评测报告：策略对比表 + 一个「评测发现了回归」的具体例子。
- 明确写出没做什么以及为什么不做。

## 10. 求职验收

投递前必须能独立演示和解释：

- Agent UI 与普通聊天 UI 有什么区别；为什么 Task、Run、Event 不能只用一条消息表示。
- 如何把模型 token、tool call、approval 和错误转换成前端状态。
- Next.js 和 FastAPI 各自负责什么，如何保持类型契约。
- 文件、搜索、Shell、Apply Patch、Git 工具如何经过校验、权限、Sandbox、审批和审计。
- Tool Calling、MCP、Skills、LangGraph 和子 Agent 委派的完整执行链路。
- Planning、Memory、Tool Use、Reflection 四要素在你的代码里分别落在哪。
- Context Engineering：一次推理的上下文由哪几部分拼成，超长时压缩掉什么。
- PostgreSQL、pgvector、Redis、对象存储和执行 Worker 分别保存或执行什么。
- 代码检索召回差时如何定位问题，并在界面上提供可调试信息。
- **你怎么知道改动之后变好了；你怎么知道你的评测本身可信。**
- 如何处理超时、重试、重复提交、路径穿越、越权、Prompt Injection、命令注入和密钥泄漏。
- 单次运行的成本和延迟是多少，怎么控制上限。
- 任务为什么能在浏览器刷新、服务重启或等待审批后继续。
- 如何在 macOS、Windows 和服务器运行同一套项目。

倒数第三条是招聘方明确点出的第一区分点，必须能用具体数字和一个真实案例回答，不能只讲方法论。

最低作品集要求：主项目可运行、可部署、可演示，包含架构图、测试、评测结果和问题复盘。


## 11. 文档关系

- [课程任务与验收](LEARNING_CURRICULUM.md)
- [当前进度与唯一下一课](LEARNING_HANDOFF.md)
- [教学与文档维护规则](AGENTS.md)
- [环境操作](ENVIRONMENT.md)
- [最终产品能力验收](docs/codex-like-agent-scope.md)
- [岗位与技能校准](SKILL_GAP_ANALYSIS.md)
