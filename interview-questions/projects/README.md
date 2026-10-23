# 项目复盘：AI 全栈开发工程师

源码核对：2026-10-22；恢复归档：2026-10-23。复习优先级不是公开面试频率；所有恢复题均为参考答案已整理、尚未模拟。

## 先区分证据来源

| 来源 | 应如何表达 | 不可混淆 |
| --- | --- | --- |
| ChatSearch 工作项目 | 已确认的个人交付＋前端真实链路 | 不能从代码存在推断个人负责 |
| assistant-ui-go / agent-gateway / pl-agent | 跨端协作理解与后端源码复盘，具体版本见基线 | 不说三个后端都由本人设计实现 |
| Learning Lab 学习项目 | 本地 Coding Agent 的工程实践；区分本人和教练实现 | 不套用为公司线上架构或指标 |
| 设计追问 | “进一步增强时我会……” | 不把建议描述为现有功能 |

已恢复原分类题共 **29 道**：项目 12 道、后端 12 道、Agent 5 道；另保留后续 20 道项目题与 7 个知识专题的深入核对。两组主题有交叉，具体执行与恢复事实以深入核对为准，不累加成互不重复的题数。

## P0 工作项目全栈主线

| 专题 | 重点 |
| --- | --- |
| [Agent 执行、Checkpoint 与工具调度最新核对](chatsearch-ai-fullstack-interview.md) | 后续 20 道项目题与 7 个知识专题；具体执行语义以此为准 |
| [源码基线与职责](chatsearch-source-baseline.md) | 本机路径、提交、分支、证据符号、验证边界 |
| [工作项目总览](chatsearch-overview.md) | 前端交互与服务端协作全景 |
| [Go 网关与 Python Agent 全链路](chatsearch-fullstack-interview.md) | 8 道项目题：接入、续传、ACK、背压、取消、终态、事务、容量 |
| [技能输入契约](chatsearch-skill-contract.md) | 4 道项目题：结构化身份、Unicode、异步快照、工具权限 |
| [Agent 执行与持久恢复](../ai/agent/checkpoint-and-tool-effects.md) | ReAct/MCP、挂起点、工具未知结果与评测 |
| [面试速查](chatsearch-interview-cheatsheet.md) | 复习顺序、证据入口、不能过度承诺的语义 |

## 独立学习项目

| 项目材料 | 范围与状态 |
| --- | --- |
| [Learning Lab 独立证据](learning-lab-evidence.md) | 本地优先 Coding Agent；FastAPI / PostgreSQL / RAG 学习实践，按既有记录举证，本次未重新验收 |

## 保留的前端深挖专题

| 专题 | 使用边界 |
| --- | --- |
| [SSE 选型与解析](chatsearch-sse-vs-websocket.md) | 传输与协议基础 |
| [SSE 恢复](chatsearch-sse-reconnect.md) / [Checkpoint](chatsearch-checkpoint-semantics.md) | 新增服务端游标对照，客户端仍是 best-effort |
| [双答案一致性](chatsearch-ab-consistency.md) / [代码状态机](chatsearch-ab-state-machine-code.md) | 分支路由与收口，不替代后端任务状态机 |
| [渲染背压](chatsearch-render-backpressure.md) | DOM 消费控制，不等于模型限速 |
| [Hybrid](chatsearch-hybrid.md) | Web 实现与 Native 协作分开 |
| [Vite/Rolldown](chatsearch-vite-rolldown.md) | 工程化优势；历史指标不能当当前测量 |

建议先讲一个工作项目完整链路，再深挖一个后端边界，最后用 Learning Lab 说明亲手实践；不要求把所有旧长文背完。

## 复盘模板

```markdown
# 项目名

> 时间：YYYY-MM ～ YYYY-MM | 角色：xxx | 技术栈：xxx

## 一句话简介（30 秒电梯陈述）

## 背景与目标（为什么做 / 解决什么问题）

## 我的职责（边界清晰，别把团队的功劳全揽下）

## 技术选型与架构（为什么选 A 不选 B）

## 难点与解决方案（重点，2～3 个，每个配「问题 → 方案 → 结果」）

## 量化成果（有数字：QPS / 延迟 / 成本 / 准确率 / 效率）

## 面试追问清单（自问自答，至少 5 条）
```

## 知识点关联

面试从项目切入、深挖技术，所以每个复盘里把涉及的知识点**反向链接**回对应目录（如 `../ai/rag-pipeline.md`、`../frontend/xxx.md`），形成「项目 → 知识点」的复习路径，避免项目故事和原理脱节。

## STAR 法则

每个项目故事按 **S（背景）→ T（任务）→ A（行动）→ R（结果）** 组织，R 使用可核验结果；没有真实数字时说明验证方法与边界，不编造收益。
