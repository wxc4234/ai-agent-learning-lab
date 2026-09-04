# 项目复盘

社招项目面考察：**你做了什么、难点在哪、怎么取舍、结果如何、能否扛追问**。这里沉淀的是「面试版项目故事」，不是代码（代码在 `apps/` 和各仓库里）。

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

每个项目故事按 **S（背景）→ T（任务）→ A（行动）→ R（结果）** 组织，R 必须有可量化数字。

## 项目索引

| 项目 | 方向 | 状态 |
| --- | --- | --- |
| `apps/api` Agent 后端（FastAPI + pgvector + RAG + Tool Calling） | AI 全栈 | 待复盘 |

<!-- ORGANIZED-INTERVIEW-IMPORT:START -->

## ChatSearch 项目材料

| 项目材料 | 方向 | 状态 |
| --- | --- | --- |
| [ChatSearch 项目总览与全链路](chatsearch-overview.md) | ChatSearch AI | 已整理 |
| [ChatSearch：SSE 选型与流解析](chatsearch-sse-vs-websocket.md) | ChatSearch AI | 已整理 |
| [ChatSearch：双答案对比一致性](chatsearch-ab-consistency.md) | ChatSearch AI | 已整理 |
| [ChatSearch：SSE 中断与断点续传](chatsearch-sse-reconnect.md) | ChatSearch AI | 已整理 |
| [ChatSearch：流式渲染背压](chatsearch-render-backpressure.md) | ChatSearch AI | 已整理 |
| [ChatSearch：Hybrid 离线包与首屏优化](chatsearch-hybrid.md) | ChatSearch AI | 已整理 |
| [ChatSearch：Vite/Rolldown 构建优化](chatsearch-vite-rolldown.md) | ChatSearch AI | 已整理 |
| [ChatSearch：双答案代码级状态机](chatsearch-ab-state-machine-code.md) | ChatSearch AI | 已整理 |
| [ChatSearch：Checkpoint 语义与一致性](chatsearch-checkpoint-semantics.md) | ChatSearch AI | 已整理 |
| [ChatSearch：面试速查](chatsearch-interview-cheatsheet.md) | ChatSearch AI | 已整理 |

<!-- ORGANIZED-INTERVIEW-IMPORT:END -->
