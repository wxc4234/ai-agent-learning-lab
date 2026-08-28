# Agent 工程师技能差距分析（路线图补强）

更新时间：2026-08-28（Asia/Shanghai）

这份文档回答一个问题：**现有 `LEARNING_HANDOFF.md` 的学习路径，和 2026 年真正的 Agent 应用开发工程师所需技能相比，缺了什么、要不要补。**

它是一份「补强建议」，不是「推翻重来」。现有"先手写理解、再上框架"的节奏是对的，本文只负责把缺的现代核心技术补进去。

---

## 1. 一句话结论

现有路径**地基很扎实**（Python / FastAPI / LLM 调用 / Tool Calling / RAG），但**重心偏"模型 + 单次工具调用"，缺少 2026 年 agent 工程真正值钱的那一半**：Agent 运行时、MCP、上下文工程、可观测性、评测、多 Agent 和沙箱安全。

其中**向量数据库其实已经在路线里了**（第 9 节 C「RAG」），只是写得太泛，需要具体化。真正完全缺失的是下面第 4 节表里那几项。

---

## 2. 参考的两个开源项目

### 2.1 OpenAI Codex / Codex Harness

- 仓库：`github.com/openai/codex`（Apache-2.0）
- CLI 前端 2025 年开源，底层 **Codex Harness（agent 运行时）** 2026-08-19 完整开源
- 核心是 Rust 写的 `codex-rs`，值得学习的是它的运行时能力：

| 能力 | 说明 |
|---|---|
| **Agent loop** | 拼 prompt（system 规则 + 工具信息 + MCP 工具列表 + AGENTS.md + 环境）→ LLM 推理出事件流（工具调用 + reasoning）→ 内部迭代直到"完成" |
| **Prompt caching** | 把 O(n²) 成本降到 O(n)，省成本、降延迟 |
| **Compaction** | 超 token 限制时把历史**总结成摘要**，而不是硬截断 |
| **Retained reasoning** | 跨工具调用保留推理状态，减少重复推理 token |
| **沙箱** | bubblewrap / Seatbelt / Windows 沙箱 + WFP 防火墙，4 档沙箱模式 |
| **MCP** | 一等公民：codex-mcp / rmcp-client / mcp-server |
| **Hooks** | session_start / pre_tool_use / post_tool_use 等生命周期钩子 |
| **多 Agent** | spawn_agent / send_input / wait_agent / close_agent |
| **持久记忆** | 两阶段（抽取 + 整合）写入 `memory_summary.md`，下次会话注入 |

### 2.2 DeepSeek Harness（DSH）

- DeepSeek 的第一款 agent 框架，理念是**"一切皆插件"（everything is a plugin）**，被称作"agent 界的 Android"
- 它的核心主张一句话概括：

> **Agent = Model + Harness（运行时）**

模型只是大脑，harness 才是把模型变成可用 agent 的那一半——负责 loop、工具编排、MCP、插件、记忆、评测。

---

## 3. 核心洞察

现有路径一直在教"模型 + 单次 tool call"的底层原理，这是必要的地基。但 Codex 和 DSH 揭示的现代 agent 工程，重心在：

1. **把 loop 抽象成可复用的「agent 运行时」**，而不是每次都手写一遍
2. **围绕这个运行时的周边设施**：MCP 接工具、上下文工程控成本、可观测性还原轨迹、评测验证质量、沙箱保安全

所以补强的方向不是"换框架"，而是**把手写的 tool loop 逐步升华成一个你自己拥有的最小 agent runtime，再补上周边设施**。

---

## 4. 差距分析表（按优先级）

| 优先级 | 缺的能力 | 为什么是核心 | 现有路径状态 |
|---|---|---|---|
| 🔴 1 | **MCP（模型上下文协议）** | 给 agent 接外部工具/数据源的标准方式，Codex 和 DSH 都是一等公民 | 完全没提 |
| 🔴 2 | **Agent 运行时 / harness 心智** | "Agent = Model + Harness"，把 loop 抽象成可复用 runtime（统一 loop + 停止条件 + 重试） | 有"抽取通用 execute_tool"，但没升华成 runtime |
| 🔴 3 | **上下文工程** | 不止滑动窗口，还有 compaction 摘要、长期记忆（抽取+整合）、prompt caching | 只有"滑动窗口 + SQLite 存消息"，无记忆整合 |
| 🟠 4 | **可观测性 / tracing** | 用 OpenTelemetry + 轨迹日志还原"模型每步看到啥、做了啥" | 只有"日志"两个字 |
| 🟠 5 | **评测（LLM-as-judge）** | 用 LLM 当评委 + 轨迹评测 + benchmark harness 思路 | "评测集"很弱 |
| 🟠 6 | **多 Agent 模式** | supervisor / 子 agent 分发（Codex 的 spawn_agent、DSH 的 agent-teams） | 没提 |
| 🟡 7 | **安全沙箱执行** | 模型生成的代码/命令要在沙箱里跑 | 只有"工具白名单 + 不用 eval" |
| 🟡 8 | **编排框架** | 最终要落到 LangGraph / OpenAI Agents SDK / DSH | 明确"先不上框架"（哲学对，但缺终点） |
| 🟡 9 | **向量数据库具体化** | 具体到 Chroma/Qdrant/pgvector + hybrid search + rerank | 已提但太泛 |
| 🟢 10 | **结构化输出** | 用 Pydantic + JSON mode 让模型稳定返回 JSON（前端转岗尤其需要） | 用 Pydantic 做请求校验，没强调输出结构化 |

---

## 5. 各缺项速查（是什么 → 为什么 → 怎么学）

### 🔴 MCP（模型上下文协议）

- **是什么**：一套标准协议，让 agent 用统一方式连接外部工具、数据源、服务。一个 MCP server 暴露若干 tool，任何 MCP client 都能调。
- **为什么**：没有它，每接一个新工具都要单独写胶水代码；有了它，工具"即插即用"，是 2026 年 agent 生态的事实标准。
- **怎么学**：先用 FastAPI/官方 SDK 写一个最小 MCP server（暴露 1～2 个工具），再用 client 连它，理解 `list_tools` / `call_tool` 语义。

### 🔴 Agent 运行时 / harness 心智

- **是什么**：一个可复用的循环——拼 prompt → LLM 推理 → 执行工具 → 结果回填 → 迭代，直到满足停止条件（拿到最终答案 / 达到最大轮次）。
- **为什么**：你现在手写的 tool loop 就是它的雏形。抽象成 runtime 后，多工具、多轮、多 agent 都能复用同一套逻辑。
- **怎么学**：把 `chat_api.py` 里的工具循环抽成一个 `run_agent(messages, tools)`，加最大循环次数、错误重试、停止条件。

### 🔴 上下文工程

- **是什么**：管理喂给模型的上下文，包括三件事：
  - **compaction**：超限时把历史**总结成摘要**（不是硬截断）
  - **长期记忆**：两阶段（抽取事实 + 整合去重）写入记忆文件，下次会话注入
  - **prompt caching**：命中缓存的重复前缀，省成本、降延迟
- **为什么**：上下文是 agent 最大的成本和最大的失忆来源，工程化的上下文管理直接决定质量和成本。
- **怎么学**：先做"超 N 轮时用模型把旧对话总结成摘要替换"，再做"把关键事实持久化成记忆"，最后了解 prompt caching 的机制。

### 🟠 可观测性 / tracing

- **是什么**：给 agent 的每一次工具调用、每一次 LLM 推理打上 trace，能还原"模型每一步看到什么、做了什么、花了多久"。
- **为什么**：agent 出问题时，没有轨迹日志就只能靠猜。DSH 的评测和 AgentLoop 都强调 trajectory（轨迹）日志的价值。
- **怎么学**：先用结构化日志记录（session_id、工具名、耗时、成功/失败），进阶接 OpenTelemetry 或 Langfuse/LangSmith。

### 🟠 评测（LLM-as-judge）

- **是什么**：用另一个 LLM 当评委，给 agent 的回答打分；或用轨迹评测判断"模型有没有走对步骤"；对标 benchmark harness（terminal-bench / dsh-eval / SWE-bench 思路）。
- **为什么**：手工测试几个例子不叫评测，无法知道改动是变好还是变坏。
- **怎么学**：先做"RAG 问答评测集 + LLM 打分"，再做"固定用例集跑 agent，对比轨迹和最终答案"。

### 🟠 多 Agent 模式

- **是什么**：一个 supervisor 把任务拆给多个子 agent 分别执行，再汇总（Codex 的 spawn_agent、DSH 的 agent-teams）。
- **为什么**：复杂任务（一个做检索、一个写代码、一个做校验）单 agent 串行效率低、易出错。
- **怎么学**：先理解"子 agent = 一个独立的 agent runtime + 独立上下文"，再用框架的 spawn/subagent 能力做一个小 demo。

### 🟡 安全沙箱执行

- **是什么**：模型生成的代码或命令在受限环境（Docker / bubblewrap / 受限 shell）里执行，无法碰宿主文件系统、网络和密钥。
- **为什么**：只要 agent 能执行代码，就存在注入/越权风险。工具白名单只能管"调哪个函数"，管不了"函数内部干了啥"。
- **怎么学**：理解沙箱模型；做"代码执行类工具"时用 Docker 容器隔离执行。

### 🟡 编排框架

- **是什么**：LangGraph、OpenAI Agents SDK、DeepSeek Harness 这类把 agent 流程图形化/声明式编排的框架。
- **为什么**：手写 loop 能理解原理，但生产项目用框架能省大量样板代码、自带观测和重试。
- **怎么学**：先手写理解底层（你正在做），再挑一个框架（建议 LangGraph 或 OpenAI Agents SDK）重构作品集项目。

### 🟡 向量数据库具体化

- **是什么**：把"向量数据库"落到具体技术——Chroma（轻量）或 Qdrant（生产级）或 pgvector（复用 PostgreSQL）。
- **为什么**：泛泛写"向量数据库"学不深；具体到某一家才能学会 hybrid search（BM25 + 向量）和 rerank（交叉编码器重排）。
- **怎么学**：选一个（建议先 Chroma 入门、再 Qdrant），做"向量检索 + BM25 混合 + rerank"的完整链路。

### 🟢 结构化输出

- **是什么**：用 Pydantic 定义输出 schema + JSON mode，让模型稳定返回结构化 JSON，而不是自由文本。
- **为什么**：前端转岗尤其需要——agent 返回结构化的数据才能被前端/下游直接消费。
- **怎么学**：给接口加一个 `response_model`，让 agent 的最终答案也走结构化输出校验。

---

## 6. 建议的增补路线（映射到现有 12 周）

不推翻现有节奏，只在关键节点插入：

| 时间 | 现有主线 | 增补内容 |
|---|---|---|
| 第 1～2 周 | Python、FastAPI、LLM、SQLite、Tool Calling | 心里把手写 loop 命名为"最小 agent runtime"（🔴2 的起点） |
| 第 3～4 周 | RAG、向量检索、引用和评测 | 向量数据库具体化（Chroma/Qdrant）+ hybrid search + rerank（🟡9）；补结构化输出（🟢10） |
| 第 5～6 周 | **新增**：上下文工程 | compaction 摘要、长期记忆（抽取+整合）、prompt caching（🔴3） |
| 第 5～6 周 | **新增**：观测 + 评测 | OpenTelemetry/Langfuse 追踪 + LLM-as-judge 评测（🟠4、🟠5） |
| 第 7～8 周 | 工作流、状态管理、多工具、测试、日志 | 引入 MCP（写一个 MCP server）（🔴1）；多 Agent 模式（🟠6）；安全沙箱（🟡7） |
| 第 9～10 周 | 部署、性能、安全和演示 | 落一个编排框架（LangGraph 或 OpenAI Agents SDK）（🟡8） |
| 第 11～12 周 | 简历、项目表达和面试准备 | 保留 |

---

## 7. 与 LEARNING_HANDOFF.md 的关系

- 本文件**只增补路线图，不改变当前断点**。
- 当前断点仍是：**无参数时间工具已完成；下一课是带参数工具 `add_numbers`**。
- "先手动理解、再上框架"的哲学保持不变，MCP / 框架 / 多 Agent 都排在打好 Tool Calling 地基之后。

---

## 8. 参考来源

- [openai/codex](https://github.com/openai/codex)
- [OpenAI Codex 源码深度研究](https://github.com/xiaonancs/codex-source-analysis)
- [Codex Harness 全面开源：三层集成接口解析](https://news.qiniu.com/archives/1787276125198)
- [Agent = Model + Harness：DeepSeek Harness 开源后如何评测 Agent 运行时](https://xie.infoq.cn/article/a939826a995decfba071a0137)
- [DeepSeek Harness: más allá de las 100.000 estrellas](https://wavect.io/es/blog/deepseek-harness-enterprise-review/)
