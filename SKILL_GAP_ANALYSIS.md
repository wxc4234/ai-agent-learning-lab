# AI Agent 应用开发岗位与技能校准

目标方向：前端 / 全栈产品工程
调研日期：2026-08-31（Asia/Shanghai）

这份文档只解释：**目标岗位是什么、市场为什么需要这些能力、路线为什么这样安排。**

具体学习步骤统一查看 [LEARNING_PLAN.md](LEARNING_PLAN.md)。

## 1. 目标岗位修正

目标不是通用的“大模型应用开发工程师”，而是：

- AI Agent 前端工程师。
- AI Agent 应用开发工程师（偏前端）。
- Agent 产品工程师。
- AI Agent 全栈应用工程师。

它与纯大模型岗位的区别：

| 方向 | 主要工作 |
|---|---|
| 大模型算法 | 训练、微调、推理优化、模型效果 |
| 通用大模型应用后端 | Python 服务、RAG、模型网关、平台能力 |
| **Agent 前端/产品工程** | 把 Agent 的计划、工具、状态、引用、审批和错误变成可用产品 |
| **Agent 全栈应用** | 同时完成 Agent 前端、API、状态、工具和数据闭环 |

你的前端经验应该成为差异化优势，而不是被 Python 学习完全替代。

## 2. 招聘调研方法

本次分两层调研：

1. 20 个国内外 Agent/LLM/RAG 广义岗位，用于确认 Agent 工程的后端和生产化底座。
2. BOSS 直聘的 Agent 前端、Agent 应用和 Agent 全栈岗位，用于校准你的准确投递方向。

这些是定向样本，不是完整市场普查；岗位下线后链接可能失效。

## 3. BOSS 目标岗位的直接信号

### 字节跳动：AI Agent 前端开发工程师

公开岗位强调：

- AI Agent 研发平台前端。
- Web、桌面端、IDE 插件等多终端。
- React/Vue、Koa、通用组件、前端架构和研发效能。
- 理解 Agent 产品与应用场景。

### 阿里巴巴：前端开发工程师－AI Agent 方向

公开岗位强调：

- React、TypeScript、状态管理、SSR 和中大型应用架构。
- REST/GraphQL 集成和独立完成前后端模块。
- 将复杂 AI 能力转化为易用的产品体验。
- 高性能、高并发、鲁棒性和生产级 Agent 平台。

### Shopee / MiniMax 等 Agent 前端岗位

公开岗位描述中直接出现：

- 对话、任务执行、工具调用等 Agent 交互界面。
- streaming 流式响应和 Agent 前端交互层。
- AI Agent / AI App 的跨端产品开发。

### Agent 全栈岗位

公开岗位中还出现：

- TypeScript 全栈 Agent 开发。
- Agent 工作流、MCP、Skills、数据库和系统集成。
- 运行日志、任务状态、错误恢复、评测和可靠性。

结论：**前端不是只负责聊天框，而是负责把非确定性的 Agent 执行过程变成透明、可控制、可恢复的产品体验。**

## 4. 广义 Agent 岗位的工程底座

此前 20 个广义岗位样本的人工标注结果：

| 能力标签 | 命中岗位数 | 对本路线的意义 |
|---|---:|---|
| Agent、工具调用或工作流 | 20 / 20 | 必须理解 Agent 执行链路 |
| Python | 19 / 20 | 偏前端也需要能与 Python Agent 服务协作 |
| LangGraph/LangChain 等框架 | 17 / 20 | 至少掌握一个主框架 |
| RAG、检索或向量数据库 | 16 / 20 | 知识型 Agent 是常见业务场景 |
| Python Web 框架 | 15 / 20 | 全栈作品需要真实 Agent 后端 |
| SQL、Redis、队列或向量存储 | 14 / 20 | Agent 有会话、任务和运行状态 |
| 评测、可观测性、可靠性或安全 | 14 / 20 | Agent 产品必须可调试和可控制 |
| Docker、云、CI/CD 或 Linux | 10 / 20 | 作品需要能部署和复现 |
| MCP | 9 / 20 | 标准工具接入能力正在进入岗位要求 |

这张表不表示目标要转成纯 Python 后端，而是说明偏前端 Agent 工程师也不能只会 React 页面。

## 5. 最终能力模型

| 优先级 | 能力 | 达到什么程度 |
|---|---|---|
| 必备 | React、TypeScript、Next.js | 能独立设计并实现生产级 Agent 界面 |
| 必备 | Streaming、SSE、WebSocket | 能处理增量事件、停止、重连和恢复 |
| 必备 | Agent UI 状态模型 | 能展示 plan、tool、approval、result 和 error |
| 必备 | Tool Calling、结构化输出 | 能从模型事件转换到类型安全的前端组件 |
| 必备 | Python、FastAPI | 能独立完成 Agent API，而不只是等待后端提供接口 |
| 必备 | PostgreSQL、Redis | 能持久化会话、任务、工具事件并处理缓存状态 |
| 重要 | LangGraph、MCP | 能实现状态工作流、工具接入和人工确认 |
| 重要 | RAG、pgvector | 能实现带引用和权限的知识型 Agent |
| 重要 | 测试、Trace、评测 | 能复现和定位 Agent 的非确定性问题 |
| 加分 | 桌面端、IDE 插件、可视化编排 | 对应字节等 Agent 开发平台岗位 |
| 暂缓 | 微调、CUDA、模型训练 | 与当前目标岗位投入产出不匹配 |

建议学习时间分配：前端产品 35%，Agent/RAG 30%，Python 后端 25%，部署安全与求职 10%。

## 6. 开源项目如何影响路线

| 项目 | 借鉴内容 | 不照搬什么 |
|---|---|---|
| [OpenAI Codex](https://github.com/openai/codex) | 跨平台工具执行、审批、沙箱、MCP、会话、Compaction 和事件流 | Rust 工程和完整系统级沙箱 |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Agent Loop、Session Event Log、Tool Registry、插件化执行管线 | 快速变化的大型插件体系 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 状态图、Checkpoint、恢复、Memory 和 Human-in-the-loop | 只背框架 API 而不理解状态 |
| [Vercel AI SDK](https://github.com/vercel/ai) | TypeScript 流式 UI、工具状态和结构化数据 | 把所有 Agent 后端都塞进 Next.js |
| [Mastra](https://github.com/mastra-ai/mastra) | TypeScript Agent、Workflow、HITL 和 Evals | 与 LangGraph 同时深入两套框架 |
| [Dify](https://github.com/langgenius/dify) | 前后端、数据库、Redis、Worker 和可观测性的完整产品结构 | 复制整个平台 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 文档、对象存储、检索、数据库和任务系统 | 一开始就部署完整重型架构 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 标准 Tool、Resource、Prompt 和传输协议 | 为每个工具继续写私有协议 |

### 在作品集中的具体映射

```text
Codex / DSH 的 Event 与 Session
→ PostgreSQL 保存 run_events

Tool Registry 与执行管线
→ Schema → 权限 → 执行 → 超时/重试 → Trace

模型和工具的流式事件
→ Next.js Agent 时间线与状态卡片

Approval / Sandbox
→ 高风险操作批准、修改、拒绝和隔离执行

MCP / Skills
→ 标准化连接外部工具
```

pgvector 不是从 Codex 或 DeepSeek Harness 照搬的。它用于 RAG 知识检索；Harness 项目主要影响 Agent Runtime、工具执行和会话架构。

## 7. 最终技术选型

- 产品前端：TypeScript + React + Next.js + Vercel AI SDK。
- Agent 后端：Python + FastAPI + Pydantic。
- 状态与数据：PostgreSQL + SQLAlchemy + Alembic + Redis。
- 向量检索：PostgreSQL + pgvector；Qdrant 只作为后续进阶。
- Agent 编排：先手写最小循环，再使用 LangGraph。
- 工具协议：MCP Python SDK。
- 生产化：pytest + Docker Compose + OpenTelemetry/Langfuse + CI/CD。

跨平台统一使用 Docker Compose。macOS 与 Windows 运行相同镜像、迁移和命令；本地数据分别保存，需要共享时再连接云数据库。

## 8. BOSS 目标岗位来源

- [字节跳动｜AI Agent 前端开发工程师－开发者服务](https://www.zhipin.com/job_detail/355bf7b1b6e7b19a0nN63928EFZY.html)
- [阿里巴巴｜前端开发工程师－AI Agent 方向](https://www.zhipin.com/job_detail/5eddf8848c51eeb9031_2ty8EFdY.html)
- [AI Agent 工程师（前端背景）](https://www.zhipin.com/job_detail/e896b1a48d4d5ba90nB43NW5GFNU.html)
- [AI Agent 全栈工程师](https://www.zhipin.com/job_detail/8295c5c9fb72ea870nJ83NW8F1RQ.html)
- [阿里 Agent 前端与 AI 前端岗位集合](https://www.zhipin.com/zhaopin/d1304da9ebe1ae391nJ-09q6Ew~~/)
- [Shopee Agent 前端与 AI 平台全栈岗位](https://www.zhipin.com/zhaopin/b88ef27f4fe6eb7c1HJ82t-6FA~~/)
- [TypeScript 全栈与 Agent 全栈岗位](https://www.zhipin.com/zhaopin/7262d16559e61b0d0Hx53Ni8/)
- [深圳 AI Agent 前端岗位集合](https://www.zhipin.com/zhaopin/70f28db8bc1a3c101HB92dm9Eg~~/)
- [MiniMax 等 Agent 前端 / AI App 岗位](https://m.zhipin.com/zhaopin/8087b552b9b44bec1Hx72t27/)
- [Agent 全栈交付方向前端架构岗位](https://www.zhipin.com/zhaopin/40e83b33ec8e69b31XR409U~/)

## 9. 广义岗位样本来源

- [瑞风协同｜AI 应用开发工程师](https://www.zhaopin.com/jobdetail/CC388480710J40822509716.htm)
- [海康威视｜高级应用软件开发－大模型](https://talent.hikvision.com/home/socity/position?postId=B4F6AAF8C5C1FEB7D6C131231EBAB46F)
- [飞享数据｜LangGraph AI Agent 工程师](https://www.zhaopin.com/jobdetail/CC245321380J40817430109.htm)
- [和利时｜智能体开发工程师](https://www.zhaopin.com/jobdetail/CC000145900J40870795001.htm)
- [中科曙光｜AI Agent 平台与框架工程师](https://www.zhaopin.com/jobdetail/CC120205180J40903014213.htm)
- [埃森哲｜AI 全栈工程师（Agent 方向）](https://www.accenture.com/cn-en/careers/jobdetails?id=14477303_en)
- [Randstad｜AI Agent + Python 后端](https://www.randstad.com/jobs/ai-agent-pythonhou-duan-kai-fa-gong-cheng-shi-_shang-hai-_46970632/)
- [Bain｜Full Stack TypeScript Engineer, AI Products](https://www.linkedin.com/jobs/view/full-stack-typescript-engineer-ai-products-dataedge-at-bain-company-4426382734)
- [SurfSense｜Software Engineer](https://job-boards.greenhouse.io/surfsense/jobs/5725619004)
- [Knit｜Senior Full Stack Engineer](https://job-boards.greenhouse.io/knit/jobs/4185589009)

## 10. 2026-08-31 复核：路线与市场的差距

在原有 20 岗位样本之外，又复核了一轮 2026 年的公开岗位描述、招聘方视角文章和面试题库统计。**结论：技术选型没有踩空，但优先级排错了。**

### 10.1 已经对上的部分

Python + async、FastAPI、LangGraph、RAG（chunking / hybrid / rerank）、向量库、MCP、Function Calling、Docker / CI、可观测性——这些在复核的岗位描述里逐条命中。腾讯 2027 校招已把 Agent 开发列为独立岗位，要求为「Python/TypeScript/Go 至少精通一门」+「深入理解 Agent 原理」+ LangGraph 等框架，本路线完全覆盖。

一个额外信号：腾讯把「熟练使用 AI 编程工具（如 Cursor、Claude Code 等）」**写进了岗位要求而非加分项**。这是零成本项，应写进简历并在项目 README 说明工作流。

### 10.2 三个结构性偏差（已修正）

| 偏差 | 市场信号 | 原计划 | 修正后 |
|---|---|---|---|
| 评测与可观测性排太后 | 招聘方称 evals 与 guardrails 是「承重技能」，面试核心问题是「你怎么知道改动之后变好了」「你怎么知道评测可信」 | 第 9 周才做 Trace 和自动评测 | 第 3 周即建 run_id 事件落库 + 5 条冒烟评测，逐周加用例；每天固定跑一次 |
| 缺 Agent 层评测 | 面试题库点名 LLM-as-Judge、AgentBench、WebArena、SWE-bench | 只有第 6 周的检索评测（命中率/MRR/引用正确率） | 第 7 周 Day 7 增加轨迹评测 + LLM-as-Judge，度量工具选择正确率与该拒答时是否拒答 |
| 术语与 JD 对不上 | JD 原文用「Planning、Memory、Tool Use、Reflection」；面试题库把「手写 ReAct loop」列为必备、把 **memory 设计称为区分度最高的领域** | 第 7 周写作「多工具、事件、停止条件、记忆」 | 按四要素重写第 7 周，显式包含 ReAct 与 Context Engineering |

第三项是表达缺口而非技术缺口——做的事情本来就对，改标题成本极低，收益是简历直接命中关键词。

### 10.3 补充的概念级内容

以下在面试中频率高但实现成本与收益不匹配，各留 2 ～ 4 小时达到「能讲清、能画图」即可，不实现：第二个 Agent 框架（JD 常写「≥2 个框架」）、多 Agent 协作与路由、A2A 与 Agent Card、Computer Use / GUI Agent、其他向量库的选型理由。

保持「只深入一个框架」的判断不变：招聘方明确把 framework name-dropping 列为**被高估**的信号，深度比广度值钱。

### 10.4 被低估的自身优势

**AG-UI 协议**（基于 SSE 的事件协议，专门定义 Agent 与用户界面如何通信，CopilotKit 构建于其上）解决的正是第 3 ～ 4 周要手搓的问题，原路线完全没提。对偏前端的目标岗位而言，这比多学一个后端框架更有价值——它等于是「Agent UI 事件应该长什么样」的现成答案，可以选择对齐，也可以说明为什么不对齐，两种都是有效回答。

### 10.5 时间与作品集策略修正

复核日期 08-31，剩余 11 周（至 11-15）。原计划第 10 ～ 11 周各用一周完成一个完整项目不现实。修正为：

- 第 3 ～ 9 周的产出全部长在同一套 `apps/api` + `apps/web` 里；第 10 ～ 11 周**只做收尾和包装，不是开始做项目**。
- 原项目一与项目二合并为一个足够深的主项目（两者共用约 80% 技术栈）。招聘方看的是 `build → measure → refine` 的具体痕迹，一个有真实评测数据、Trace 和失败恢复演示的项目胜过两个只有顺利路径的 Demo。
- 第二项目改为第 9 周末评估后再决定。
- **第 8 周开始第一批投递**，不等作品集完成。届时已有流式 UI、工具过程、RAG 带引用、ReAct + LangGraph、评测和 Trace，足以支撑面试对话；面试反馈能反向指导第 9 ～ 12 周补什么。
- 第 9 周增加云部署，让作品有公网地址。JD 普遍要求 AWS/Azure/GCP 之一，且「能点开就用」与「只能本地跑」在投递时说服力差距明显。

### 10.6 本次复核来源

- [The Complete AI Agent Engineer Skills Stack You Need in 2026](https://www.mygreatlearning.com/blog/the-complete-ai-agent-engineer-skills-stack-you-need-in-2026/)
- [AI Agent Engineer: Job Description, Skills & Salary 2026](https://www.techademy.com/ai-agent-engineer-job-salary-2026)
- [How to Recruit AI Agent Engineers in 2026](https://www.herohunt.ai/blog/how-to-recruit-ai-agent-engineers-in-2026/)（招聘方视角，evals/guardrails 为承重技能的出处）
- [腾讯 2027 校招拆解：Agent 开发成了独立岗位](https://www.cnblogs.com/itech/p/22482094)
- [面试 AI Agent 工程师会被问什么？40+ 真题 + 知识图谱](https://www.cnblogs.com/itech/p/20111938)（必问项与 memory 区分度的出处）
- [Prompt 工程师正在消失，Harness 工程师正在崛起](https://www.cnblogs.com/badhope/p/22485208/prompt-to-harness-engineering-2026)（失败复合效应 `0.95²⁰ ≈ 36%` 的出处）
- [AG-UI 协议文档](https://docs.copilotkit.ai/agno/ag-ui)
- [Context Engineering: A Practical Guide for AI Agents (2026)](https://sourcegraph.com/blog/context-engineering)

这些同样是定向样本而非市场普查，其中数篇为行业博客而非一手 JD，用于判断趋势和优先级，不作为唯一依据。
